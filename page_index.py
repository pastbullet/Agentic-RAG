import os
import json
import argparse
import copy
import math
import random
import re
from rapidfuzz import fuzz  # 需要 pip install rapidfuzz
try:
    from .utils import *
except ImportError:
    from utils import *
from concurrent.futures import ThreadPoolExecutor, as_completed


################### check title in page #########################################################
async def check_title_appearance(item, page_list, start_index=1, model=None):
    title = item['title'].strip()
    structure = item.get('structure')  # 例如 "4.2.1"
    
    # 物理页码越界检查 (无需 LLM)
    if 'physical_index' not in item or item['physical_index'] is None:
        return {'list_index': item.get('list_index'), 'answer': 'no', 'title': title, 'page_number': None}
    
    page_number = item['physical_index']
    real_idx = page_number - start_index
    if real_idx < 0 or real_idx >= len(page_list):
        return {'list_index': item.get('list_index'), 'answer': 'no', 'title': title, 'page_number': page_number}

    page_text = page_list[real_idx][0]
    
    # --- 阶段 1: 强规则匹配 (正则匹配章节号 + 标题) ---
    # 解决 FC-LS 中大量重复子标题 (Description, Protocol) 的问题
    if structure:
        # 构建正则: 允许 4.2.1 与 Title 之间有空格、换行或点
        # 能够匹配: "4.2.1 Description", "4.2.1. Description", "4.2.1\nDescription"
        safe_structure = re.escape(str(structure))
        safe_title = re.escape(title)
        
        # 尝试匹配完整结构
        pattern_str = rf"{safe_structure}\.?\s*{safe_title}"
        if re.search(pattern_str, page_text, re.IGNORECASE):
            return {'list_index': item.get('list_index'), 'answer': 'yes', 'title': title, 'page_number': page_number}
            
        # 仅匹配章节号 (如果章节号很长，如 4.2.10.1，可信度极高)
        if len(structure.split('.')) >= 3:
             # 简单匹配章节号出现在行首的情况
            if re.search(rf"(^|\n)\s*{safe_structure}\.?\s", page_text):
                 return {'list_index': item.get('list_index'), 'answer': 'yes', 'title': title, 'page_number': page_number}

    # --- 阶段 2: 模糊匹配 (Fuzzy Match) ---
    # 仅在页面前 30% 区域搜索标题 (通常标题不会在页面底部)
    # 这能有效避免匹配到正文中对他人的引用 ("see section X")
    scan_limit = int(len(page_text) * 0.4) + 500
    header_text = page_text[:scan_limit]
    
    # 使用 partial_ratio 允许标题是行的一部分
    score = fuzz.partial_ratio(title.lower(), header_text.lower())
    
    if score >= 90:
        return {'list_index': item.get('list_index'), 'answer': 'yes', 'title': title, 'page_number': page_number}
    
    # --- 阶段 3: LLM 兜底 (仅针对疑难杂症) ---
    # 只有当正则和高置信度模糊匹配都失败时，才调用 LLM
    # 这里保留你原本的 LLM 逻辑，但它现在极少被触发
    prompt = f"""
    Your job is to check if the given section appears or starts in the given page_text.
    The given section title is {title}.
    The given page_text is {page_text}.
    Reply format: {{"answer": "yes or no"}}
    Directly return the final JSON structure.
    """
    # ... (保留原有的 ChatGPT_API_async 调用) ...
    # 考虑到你不想大量改代码，这里直接复用原有逻辑，但它被上面的 return 拦截了 90%
    
    response = await ChatGPT_API_async(model=model, prompt=prompt)
    response = extract_json(response)
    answer = response.get('answer', 'no')
    
    return {'list_index': item['list_index'], 'answer': answer, 'title': title, 'page_number': page_number}

async def check_title_appearance_in_start(title, page_text, model=None, logger=None):    
    prompt = f"""
    You will be given the current section title and the current page_text.
    Your job is to check if the current section starts in the beginning of the given page_text.
    If there are other contents before the current section title, then the current section does not start in the beginning of the given page_text.
    If the current section title is the first content in the given page_text, then the current section starts in the beginning of the given page_text.

    Note: do fuzzy matching, ignore any space inconsistency in the page_text.

    The given section title is {title}.
    The given page_text is {page_text}.
    
    reply format:
    {{
        "thinking": <why do you think the section appears or starts in the page_text>
        "start_begin": "yes or no" (yes if the section starts in the beginning of the page_text, no otherwise)
    }}
    Directly return the final JSON structure. Do not output anything else."""

    response = await ChatGPT_API_async(model=model, prompt=prompt)
    response = extract_json(response)
    if logger:
        logger.info(f"Response: {response}")
    return response.get("start_begin", "no")


def appear_start_rule(item, page_list, top_n_lines=30):
    """
    规则优先判定章节是否“从页首附近开始”，命中则无需 LLM。
    """
    try:
        physical_index = int(item.get("physical_index"))
    except Exception:
        return False

    if physical_index < 1 or physical_index > len(page_list):
        return False

    page_text = str(page_list[physical_index - 1][0] or "")
    lines = page_text.splitlines()[: max(int(top_n_lines or 30), 1)]

    structure = item.get("structure")
    if structure:
        pattern = re.compile(r"^\s*" + re.escape(str(structure).strip()) + r"\b")
        for line in lines:
            if pattern.search(line):
                return True

    title = str(item.get("raw_title") or item.get("title") or "").strip().lower()
    if title:
        normalized_title = " ".join(title.split())
        for line in lines:
            normalized_line = " ".join(str(line).strip().lower().split())
            if normalized_title and normalized_title in normalized_line:
                return True

    return False


async def check_title_appearance_in_start_concurrent(structure, page_list, model=None, logger=None):
    if logger:
        logger.info("Checking title appearance in start concurrently")
    
    # skip items without physical_index
    for item in structure:
        if item.get('physical_index') is None:
            item['appear_start'] = 'no'

    # only for items with valid physical_index
    tasks = []
    valid_items = []
    for item in structure:
        if item.get('physical_index') is not None:
            if (
                item.get("structure") is None
                and item.get("start_line") is not None
                and item.get("end_line") is not None
            ) or item.get("toc_span"):
                item["appear_start"] = "yes"
                continue
            if appear_start_rule(item, page_list, top_n_lines=30):
                item["appear_start"] = "yes"
                continue
            page_idx = int(item['physical_index']) - 1
            if page_idx < 0 or page_idx >= len(page_list):
                item["appear_start"] = "no"
                continue
            page_text = page_list[page_idx][0]
            tasks.append(check_title_appearance_in_start(item['title'], page_text, model=model, logger=logger))
            valid_items.append(item)

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for item, result in zip(valid_items, results):
        if isinstance(result, Exception):
            if logger:
                logger.error(f"Error checking start for {item['title']}: {result}")
            item['appear_start'] = 'no'
        else:
            item['appear_start'] = result

    return structure


def toc_detector_single_page(content, model=None):
    prompt = f"""
    Your job is to detect if there is a table of content provided in the given text.

    Given text: {content}

    return the following JSON format:
    {{
        "thinking": <why do you think there is a table of content in the given text>
        "toc_detected": "<yes or no>",
    }}

    Directly return the final JSON structure. Do not output anything else.
    Please note: abstract,summary, notation list, figure list, table list, etc. are not table of contents."""

    response = ChatGPT_API(model=model, prompt=prompt)
    # print('response', response)
    json_content = extract_json(response)    
    return json_content.get('toc_detected', 'no')


def check_if_toc_extraction_is_complete(content, toc, model=None):
    prompt = f"""
    You are given a partial document  and a  table of contents.
    Your job is to check if the  table of contents is complete, which it contains all the main sections in the partial document.

    Reply format:
    {{
        "thinking": <why do you think the table of contents is complete or not>
        "completed": "yes" or "no"
    }}
    Directly return the final JSON structure. Do not output anything else."""

    prompt = prompt + '\n Document:\n' + content + '\n Table of contents:\n' + toc
    response = ChatGPT_API(model=model, prompt=prompt)
    json_content = extract_json(response)
    return json_content.get('completed', 'no')


def check_if_toc_transformation_is_complete(content, toc, model=None):
    prompt = f"""
    You are given a raw table of contents and a  table of contents.
    Your job is to check if the  table of contents is complete.

    Reply format:
    {{
        "thinking": <why do you think the cleaned table of contents is complete or not>
        "completed": "yes" or "no"
    }}
    Directly return the final JSON structure. Do not output anything else."""

    prompt = prompt + '\n Raw Table of contents:\n' + content + '\n Cleaned Table of contents:\n' + toc
    response = ChatGPT_API(model=model, prompt=prompt)
    json_content = extract_json(response)
    return json_content.get('completed', 'no')

def extract_toc_content(content, model=None):
    prompt = f"""
    Your job is to extract the full table of contents from the given text, replace ... with :

    Given text: {content}

    Directly return the full table of contents content. Do not output anything else."""

    response, finish_reason = ChatGPT_API_with_finish_reason(model=model, prompt=prompt)
    
    if_complete = check_if_toc_transformation_is_complete(content, response, model)
    if if_complete == "yes" and finish_reason == "finished":
        return response
    
    chat_history = [
        {"role": "user", "content": prompt}, 
        {"role": "assistant", "content": response},    
    ]
    prompt = f"""please continue the generation of table of contents , directly output the remaining part of the structure"""
    new_response, finish_reason = ChatGPT_API_with_finish_reason(model=model, prompt=prompt, chat_history=chat_history)
    response = response + new_response
    if_complete = check_if_toc_transformation_is_complete(content, response, model)
    
    while not (if_complete == "yes" and finish_reason == "finished"):
        chat_history = [
            {"role": "user", "content": prompt}, 
            {"role": "assistant", "content": response},    
        ]
        prompt = f"""please continue the generation of table of contents , directly output the remaining part of the structure"""
        new_response, finish_reason = ChatGPT_API_with_finish_reason(model=model, prompt=prompt, chat_history=chat_history)
        response = response + new_response
        if_complete = check_if_toc_transformation_is_complete(content, response, model)
        
        # Optional: Add a maximum retry limit to prevent infinite loops
        if len(chat_history) > 5:  # Arbitrary limit of 10 attempts
            raise Exception('Failed to complete table of contents after maximum retries')
    
    return response

def detect_page_index(toc_content, model=None):
    print('start detect_page_index')
    prompt = f"""
    You will be given a table of contents.

    Your job is to detect if there are page numbers/indices given within the table of contents.

    Given text: {toc_content}

    Reply format:
    {{
        "thinking": <why do you think there are page numbers/indices given within the table of contents>
        "page_index_given_in_toc": "<yes or no>"
    }}
    Directly return the final JSON structure. Do not output anything else."""

    response = ChatGPT_API(model=model, prompt=prompt)
    json_content = extract_json(response)
    return json_content.get('page_index_given_in_toc', 'no')

def toc_extractor(page_list, toc_page_list, model):
    def transform_dots_to_colon(text):
        text = re.sub(r'\.{5,}', ': ', text)
        # Handle dots separated by spaces
        text = re.sub(r'(?:\. ){5,}\.?', ': ', text)
        return text
    
    toc_content = ""
    for page_index in toc_page_list:
        toc_content += page_list[page_index][0]
    toc_content = transform_dots_to_colon(toc_content)
    has_page_index = detect_page_index(toc_content, model=model)
    
    return {
        "toc_content": toc_content,
        "page_index_given_in_toc": has_page_index
    }




def toc_index_extractor(toc, content, model=None):
    print('start toc_index_extractor')
    toc_extractor_prompt = """
    You are given a table of contents in a json format and several pages of a document, your job is to add the physical_index to the table of contents in the json format.

    The provided pages contains tags like <physical_index_X> and <physical_index_X> to indicate the physical location of the page X.

    The structure variable is the numeric system which represents the index of the hierarchy section in the table of contents. For example, the first section has structure index 1, the first subsection has structure index 1.1, the second subsection has structure index 1.2, etc.

    The response should be in the following JSON format: 
    [
        {
            "structure": <structure index, "x.x.x" or None> (string),
            "title": <title of the section>,
            "physical_index": "<physical_index_X>" (keep the format)
        },
        ...
    ]

    Only add the physical_index to the sections that are in the provided pages.
    If the section is not in the provided pages, do not add the physical_index to it.
    Directly return the final JSON structure. Do not output anything else."""

    prompt = toc_extractor_prompt + '\nTable of contents:\n' + str(toc) + '\nDocument pages:\n' + content
    response = ChatGPT_API(model=model, prompt=prompt)
    json_content = extract_json(response)    
    return json_content



def toc_transformer(toc_content, model=None):
    print('start toc_transformer')
    init_prompt = """
    You are given a table of contents, You job is to transform the whole table of content into a JSON format included table_of_contents.

    structure is the numeric system which represents the index of the hierarchy section in the table of contents. For example, the first section has structure index 1, the first subsection has structure index 1.1, the second subsection has structure index 1.2, etc.

    The response should be in the following JSON format: 
    {
    table_of_contents: [
        {
            "structure": <structure index, "x.x.x" or None> (string),
            "title": <title of the section>,
            "page": <page number or None>,
        },
        ...
        ],
    }
    You should transform the full table of contents in one go.
    Directly return the final JSON structure, do not output anything else. """

    prompt = init_prompt + '\n Given table of contents\n:' + toc_content
    last_complete, finish_reason = ChatGPT_API_with_finish_reason(model=model, prompt=prompt)
    if_complete = check_if_toc_transformation_is_complete(toc_content, last_complete, model)
    if if_complete == "yes" and finish_reason == "finished":
        last_complete = extract_json(last_complete)
        cleaned_response=convert_page_to_int(last_complete['table_of_contents'])
        return cleaned_response
    
    last_complete = get_json_content(last_complete)
    while not (if_complete == "yes" and finish_reason == "finished"):
        position = last_complete.rfind('}')
        if position != -1:
            last_complete = last_complete[:position+2]
        prompt = f"""
        Your task is to continue the table of contents json structure, directly output the remaining part of the json structure.
        The response should be in the following JSON format: 

        The raw table of contents json structure is:
        {toc_content}

        The incomplete transformed table of contents json structure is:
        {last_complete}

        Please continue the json structure, directly output the remaining part of the json structure."""

        new_complete, finish_reason = ChatGPT_API_with_finish_reason(model=model, prompt=prompt)

        if new_complete.startswith('```json'):
            new_complete =  get_json_content(new_complete)
            last_complete = last_complete+new_complete

        if_complete = check_if_toc_transformation_is_complete(toc_content, last_complete, model)
        

    last_complete = json.loads(last_complete)

    cleaned_response=convert_page_to_int(last_complete['table_of_contents'])
    return cleaned_response
    



def find_toc_pages(start_page_index, page_list, opt, logger=None):
    print('start find_toc_pages')
    last_page_is_yes = False
    toc_page_list = []
    i = start_page_index
    
    while i < len(page_list):
        # Only check beyond max_pages if we're still finding TOC pages
        if i >= opt.toc_check_page_num and not last_page_is_yes:
            break
        detected_result = toc_detector_single_page(page_list[i][0],model=opt.model)
        if detected_result == 'yes':
            if logger:
                logger.info(f'Page {i} has toc')
            toc_page_list.append(i)
            last_page_is_yes = True
        elif detected_result == 'no' and last_page_is_yes:
            if logger:
                logger.info(f'Found the last page with toc: {i-1}')
            break
        i += 1
    
    if not toc_page_list and logger:
        logger.info('No toc found')
        
    return toc_page_list

def remove_page_number(data):
    if isinstance(data, dict):
        data.pop('page_number', None)  
        for key in list(data.keys()):
            if 'nodes' in key:
                remove_page_number(data[key])
    elif isinstance(data, list):
        for item in data:
            remove_page_number(item)
    return data

def extract_matching_page_pairs(toc_page, toc_physical_index, start_page_index):
    pairs = []
    for phy_item in toc_physical_index:
        for page_item in toc_page:
            if phy_item.get('title') == page_item.get('title'):
                physical_index = phy_item.get('physical_index')
                if physical_index is not None and int(physical_index) >= start_page_index:
                    pairs.append({
                        'title': phy_item.get('title'),
                        'page': page_item.get('page'),
                        'physical_index': physical_index
                    })
    return pairs


def calculate_page_offset(pairs):
    differences = []
    for pair in pairs:
        try:
            physical_index = pair['physical_index']
            page_number = pair['page']
            difference = physical_index - page_number
            differences.append(difference)
        except (KeyError, TypeError):
            continue
    
    if not differences:
        return None
    
    difference_counts = {}
    for diff in differences:
        difference_counts[diff] = difference_counts.get(diff, 0) + 1
    
    most_common = max(difference_counts.items(), key=lambda x: x[1])[0]
    
    return most_common

def add_page_offset_to_toc_json(data, offset):
    for i in range(len(data)):
        if data[i].get('page') is not None and isinstance(data[i]['page'], int):
            data[i]['physical_index'] = data[i]['page'] + offset
            del data[i]['page']
    
    return data



def page_list_to_group_text(page_contents, token_lengths, max_tokens=20000, overlap_page=1):    
    num_tokens = sum(token_lengths)
    
    if num_tokens <= max_tokens:
        # merge all pages into one text
        page_text = "".join(page_contents)
        return [page_text]
    
    subsets = []
    current_subset = []
    current_token_count = 0

    expected_parts_num = math.ceil(num_tokens / max_tokens)
    average_tokens_per_part = math.ceil(((num_tokens / expected_parts_num) + max_tokens) / 2)
    
    for i, (page_content, page_tokens) in enumerate(zip(page_contents, token_lengths)):
        if current_token_count + page_tokens > average_tokens_per_part:

            subsets.append(''.join(current_subset))
            # Start new subset from overlap if specified
            overlap_start = max(i - overlap_page, 0)
            current_subset = page_contents[overlap_start:i]
            current_token_count = sum(token_lengths[overlap_start:i])
        
        # Add current page to the subset
        current_subset.append(page_content)
        current_token_count += page_tokens

    # Add the last subset if it contains any pages
    if current_subset:
        subsets.append(''.join(current_subset))
    
    print('divide page_list to groups', len(subsets))
    return subsets

def add_page_number_to_toc(part, structure, model=None):
    fill_prompt_seq = """
    You are given an JSON structure of a document and a partial part of the document. Your task is to check if the title that is described in the structure is started in the partial given document.

    The provided text contains tags like <physical_index_X> and <physical_index_X> to indicate the physical location of the page X. 

    If the full target section starts in the partial given document, insert the given JSON structure with the "start": "yes", and "start_index": "<physical_index_X>".

    If the full target section does not start in the partial given document, insert "start": "no",  "start_index": None.

    The response should be in the following format. 
        [
            {
                "structure": <structure index, "x.x.x" or None> (string),
                "title": <title of the section>,
                "start": "<yes or no>",
                "physical_index": "<physical_index_X> (keep the format)" or None
            },
            ...
        ]    
    The given structure contains the result of the previous part, you need to fill the result of the current part, do not change the previous result.
    Directly return the final JSON structure. Do not output anything else."""

    prompt = fill_prompt_seq + f"\n\nCurrent Partial Document:\n{part}\n\nGiven Structure\n{json.dumps(structure, indent=2)}\n"
    current_json_raw = ChatGPT_API(model=model, prompt=prompt)
    json_result = extract_json(current_json_raw)
    
    for item in json_result:
        if 'start' in item:
            del item['start']
    return json_result


def remove_first_physical_index_section(text):
    """
    Removes the first section between <physical_index_X> and <physical_index_X> tags,
    and returns the remaining text.
    """
    pattern = r'<physical_index_\d+>.*?<physical_index_\d+>'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        # Remove the first matched section
        return text.replace(match.group(0), '', 1)
    return text

### add verify completeness
def generate_toc_continue(toc_content, part, model="gpt-4o-2024-11-20"):
    print('start generate_toc_continue')
    prompt = """
    You are an expert in extracting hierarchical tree structure.
    You are given a tree structure of the previous part and the text of the current part.
    Your task is to continue the tree structure from the previous part to include the current part.

    The structure variable is the numeric system which represents the index of the hierarchy section in the table of contents. For example, the first section has structure index 1, the first subsection has structure index 1.1, the second subsection has structure index 1.2, etc.

    For the title, you need to extract the original title from the text, only fix the space inconsistency.

    The provided text contains tags like <physical_index_X> and <physical_index_X> to indicate the start and end of page X. \
    
    For the physical_index, you need to extract the physical index of the start of the section from the text. Keep the <physical_index_X> format.

    The response should be in the following format. 
        [
            {
                "structure": <structure index, "x.x.x"> (string),
                "title": <title of the section, keep the original title>,
                "physical_index": "<physical_index_X> (keep the format)"
            },
            ...
        ]    

    Directly return the additional part of the final JSON structure. Do not output anything else."""

    prompt = prompt + '\nGiven text\n:' + part + '\nPrevious tree structure\n:' + json.dumps(toc_content, indent=2)
    response, finish_reason = ChatGPT_API_with_finish_reason(model=model, prompt=prompt)
    if finish_reason == 'finished':
        return extract_json(response)
    else:
        raise Exception(f'finish reason: {finish_reason}')
    
### add verify completeness
def generate_toc_init(part, model=None):
    print('start generate_toc_init')
    prompt = """
    You are an expert in extracting hierarchical tree structure, your task is to generate the tree structure of the document.

    The structure variable is the numeric system which represents the index of the hierarchy section in the table of contents. For example, the first section has structure index 1, the first subsection has structure index 1.1, the second subsection has structure index 1.2, etc.

    For the title, you need to extract the original title from the text, only fix the space inconsistency.

    The provided text contains tags like <physical_index_X> and <physical_index_X> to indicate the start and end of page X. 

    For the physical_index, you need to extract the physical index of the start of the section from the text. Keep the <physical_index_X> format.

    The response should be in the following format. 
        [
            {{
                "structure": <structure index, "x.x.x"> (string),
                "title": <title of the section, keep the original title>,
                "physical_index": "<physical_index_X> (keep the format)"
            }},
            
        ],


    Directly return the final JSON structure. Do not output anything else."""

    prompt = prompt + '\nGiven text\n:' + part
    response, finish_reason = ChatGPT_API_with_finish_reason(model=model, prompt=prompt)

    if finish_reason == 'finished':
         return extract_json(response)
    else:
        raise Exception(f'finish reason: {finish_reason}')


def _cleanup_heading_title(raw_title):
    title = " ".join(str(raw_title or "").strip().split())
    title = re.sub(r"\.{3,}\s*\d+\s*$", "", title).strip()
    title = re.sub(r"\s+\d+\s*$", "", title).strip()
    title = re.sub(r"^[\.\-:;,)]+", "", title).strip()
    return title


def extract_sub_toc_by_headings(node_page_list, start_index, parent_structure=None, max_lines_per_page=0):
    """
    轻量子目录抽取：扫描每页前若干行的编号标题，不依赖 LLM。
    返回格式与 process_no_toc 一致：
    [{'structure': 'x.x', 'title': '...', 'physical_index': int}, ...]
    """

    if not isinstance(node_page_list, list) or not node_page_list:
        return []

    try:
        base_physical = int(start_index)
    except Exception:
        base_physical = 1

    parent = str(parent_structure).strip() if parent_structure else None
    pattern = re.compile(r"^\s*(\d+(?:\.\d+){1,9})\.?\s+(.+?)\s*$")

    seen = set()
    results = []
    max_lines = int(max_lines_per_page or 0)

    for local_page_idx, page in enumerate(node_page_list):
        if isinstance(page, (list, tuple)) and page:
            page_text = page[0]
        else:
            page_text = page
        all_lines = str(page_text or "").splitlines()
        lines = all_lines if max_lines <= 0 else all_lines[:max_lines]
        physical_index = base_physical + local_page_idx

        for line in lines:
            match = pattern.match(str(line or ""))
            if not match:
                continue

            structure = str(match.group(1) or "").strip().strip(".")
            title = _cleanup_heading_title(match.group(2))
            if not structure or not title:
                continue

            if parent and not (structure == parent or structure.startswith(parent + ".")):
                continue

            dedupe_key = (physical_index, structure)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            results.append(
                {
                    "structure": structure,
                    "title": title,
                    "physical_index": physical_index,
                }
            )

    results.sort(key=lambda item: (item["physical_index"], str(item["structure"]).count("."), str(item["structure"])))
    return results

def process_no_toc(page_list, start_index=1, model=None, logger=None):
    page_contents=[]
    token_lengths=[]
    for page_index in range(start_index, start_index+len(page_list)):
        page_text = f"<physical_index_{page_index}>\n{page_list[page_index-start_index][0]}\n<physical_index_{page_index}>\n\n"
        page_contents.append(page_text)
        token_lengths.append(count_tokens(page_text, model))
    group_texts = page_list_to_group_text(page_contents, token_lengths)
    logger.info(f'len(group_texts): {len(group_texts)}')

    toc_with_page_number= generate_toc_init(group_texts[0], model)
    for group_text in group_texts[1:]:
        toc_with_page_number_additional = generate_toc_continue(toc_with_page_number, group_text, model)    
        toc_with_page_number.extend(toc_with_page_number_additional)
    logger.info(f'generate_toc: {toc_with_page_number}')

    toc_with_page_number = convert_physical_index_to_int(toc_with_page_number)
    logger.info(f'convert_physical_index_to_int: {toc_with_page_number}')

    return toc_with_page_number

def process_toc_no_page_numbers(toc_content, toc_page_list, page_list,  start_index=1, model=None, logger=None):
    page_contents=[]
    token_lengths=[]
    toc_content = toc_transformer(toc_content, model)
    logger.info(f'toc_transformer: {toc_content}')
    for page_index in range(start_index, start_index+len(page_list)):
        page_text = f"<physical_index_{page_index}>\n{page_list[page_index-start_index][0]}\n<physical_index_{page_index}>\n\n"
        page_contents.append(page_text)
        token_lengths.append(count_tokens(page_text, model))
    
    group_texts = page_list_to_group_text(page_contents, token_lengths)
    logger.info(f'len(group_texts): {len(group_texts)}')

    toc_with_page_number=copy.deepcopy(toc_content)
    for group_text in group_texts:
        toc_with_page_number = add_page_number_to_toc(group_text, toc_with_page_number, model)
    logger.info(f'add_page_number_to_toc: {toc_with_page_number}')

    toc_with_page_number = convert_physical_index_to_int(toc_with_page_number)
    logger.info(f'convert_physical_index_to_int: {toc_with_page_number}')

    return toc_with_page_number



def process_toc_with_page_numbers(toc_content, toc_page_list, page_list, toc_check_page_num=None, model=None, logger=None):
    toc_with_page_number = toc_transformer(toc_content, model)
    logger.info(f'toc_with_page_number: {toc_with_page_number}')

    toc_no_page_number = remove_page_number(copy.deepcopy(toc_with_page_number))
    
    start_page_index = toc_page_list[-1] + 1
    main_content = ""
    for page_index in range(start_page_index, min(start_page_index + toc_check_page_num, len(page_list))):
        main_content += f"<physical_index_{page_index+1}>\n{page_list[page_index][0]}\n<physical_index_{page_index+1}>\n\n"

    toc_with_physical_index = toc_index_extractor(toc_no_page_number, main_content, model)
    logger.info(f'toc_with_physical_index: {toc_with_physical_index}')

    toc_with_physical_index = convert_physical_index_to_int(toc_with_physical_index)
    logger.info(f'toc_with_physical_index: {toc_with_physical_index}')

    matching_pairs = extract_matching_page_pairs(toc_with_page_number, toc_with_physical_index, start_page_index)
    logger.info(f'matching_pairs: {matching_pairs}')

    offset = calculate_page_offset(matching_pairs)
    logger.info(f'offset: {offset}')

    toc_with_page_number = add_page_offset_to_toc_json(toc_with_page_number, offset)
    logger.info(f'toc_with_page_number: {toc_with_page_number}')

    toc_with_page_number = process_none_page_numbers(toc_with_page_number, page_list, model=model)
    logger.info(f'toc_with_page_number: {toc_with_page_number}')

    return toc_with_page_number



##check if needed to process none page numbers
def process_none_page_numbers(toc_items, page_list, start_index=1, model=None):
    for i, item in enumerate(toc_items):
        if "physical_index" not in item:
            # logger.info(f"fix item: {item}")
            # Find previous physical_index
            prev_physical_index = 0  # Default if no previous item exists
            for j in range(i - 1, -1, -1):
                if toc_items[j].get('physical_index') is not None:
                    prev_physical_index = toc_items[j]['physical_index']
                    break
            
            # Find next physical_index
            next_physical_index = -1  # Default if no next item exists
            for j in range(i + 1, len(toc_items)):
                if toc_items[j].get('physical_index') is not None:
                    next_physical_index = toc_items[j]['physical_index']
                    break

            page_contents = []
            for page_index in range(prev_physical_index, next_physical_index+1):
                # Add bounds checking to prevent IndexError
                list_index = page_index - start_index
                if list_index >= 0 and list_index < len(page_list):
                    page_text = f"<physical_index_{page_index}>\n{page_list[list_index][0]}\n<physical_index_{page_index}>\n\n"
                    page_contents.append(page_text)
                else:
                    continue

            item_copy = copy.deepcopy(item)
            del item_copy['page']
            result = add_page_number_to_toc(page_contents, item_copy, model)
            if isinstance(result[0]['physical_index'], str) and result[0]['physical_index'].startswith('<physical_index'):
                item['physical_index'] = int(result[0]['physical_index'].split('_')[-1].rstrip('>').strip())
                del item['page']
    
    return toc_items




FRONT_HEADINGS = [
    "Abstract",
    "Status of This Memo",
    "Copyright Notice",
    "Table of Contents",
]
FRONT_HEADING_ALIASES = {
    "abstract": "Abstract",
    "status of this memo": "Status of This Memo",
    "copyright notice": "Copyright Notice",
    "table of contents": "Table of Contents",
    "contents": "Table of Contents",
    "toc": "Table of Contents",
}
FRONT_NODE_ID_MAP = {
    "Abstract": "front_abstract",
    "Status of This Memo": "front_status",
    "Copyright Notice": "front_copyright",
    "Table of Contents": "front_toc",
}
FRONT_RETRIEVAL_DISABLED_TITLES = set(FRONT_HEADINGS) | {"Front Matter"}


def is_toc_continuation_page(page_text):
    lines = [line.strip() for line in str(page_text or "").splitlines() if line.strip()]
    toc_like_lines = 0
    for line in lines:
        if re.search(r"\.{3,}\s*\d+\s*$", line):
            toc_like_lines += 1
        elif len(line) > 25 and re.search(r"\s\d+\s*$", line):
            toc_like_lines += 1
    return toc_like_lines >= 5


def find_section_heading_line(page_text, structure, title=None):
    lines = str(page_text or "").splitlines()

    if structure and title:
        strict_pattern = re.compile(rf"^\s*{re.escape(structure)}\.\s+{re.escape(str(title).strip())}\b", flags=re.IGNORECASE)
        for line_idx, line in enumerate(lines):
            if strict_pattern.search(line):
                return line_idx

    if structure:
        fallback_pattern = re.compile(rf"^\s*{re.escape(structure)}\.\s+\S+")
        for line_idx, line in enumerate(lines):
            if fallback_pattern.search(line) and not re.search(r"\.{3,}\s*\d+\s*$", line):
                return line_idx
    return None


def _extract_clean_section_title(item, structure):
    title = str(item.get("title") or "").strip()
    if not title:
        return None

    cleaned = re.sub(
        rf"^\s*{re.escape(structure)}(?:\s*\.\s*|\s+)",
        "",
        title,
        count=1,
    ).strip()
    return cleaned or title


def _get_first_body_section_info(toc_items):
    """
    在扁平 TOC 中定位第一条正文锚点（仅按 structure 判定，不依赖 title）。
    返回:
    {
      "first_section_page": int,
      "first_section_structure": str|None,
      "first_section_title": str|None,
    }
    """

    body_pattern = re.compile(r"^[1-9]\d*(\.\d+)*$")
    first_item = None

    for item in toc_items or []:
        structure_raw = item.get("structure")
        if structure_raw is None:
            continue
        structure = str(structure_raw).strip()
        if not body_pattern.match(structure):
            continue

        page = convert_physical_index_to_int(item.get("physical_index", item.get("start_index")))
        if not isinstance(page, int) or page < 1:
            continue

        candidate = {
            "page": page,
            "structure": structure,
            "title": _extract_clean_section_title(item, structure),
        }
        if first_item is None or page < first_item["page"]:
            first_item = candidate

    if not first_item:
        return {
            "first_section_page": 1,
            "first_section_structure": None,
            "first_section_title": None,
        }

    return {
        "first_section_page": first_item["page"],
        "first_section_structure": first_item["structure"],
        "first_section_title": first_item["title"],
    }


def _normalize_front_heading_text(line_text):
    normalized = " ".join(str(line_text or "").strip().split())
    normalized = re.sub(r"\.{2,}\s*\d+\s*$", "", normalized).strip()
    normalized = re.sub(r"\s+\d+\s*$", "", normalized).strip()
    normalized = normalized.strip(" \t\r\n-_:;,.()[]{}<>")
    normalized = " ".join(normalized.split()).lower()
    return normalized


def _canonical_front_heading(line_text, line_index=None, toc_heading_max_line=40):
    normalized = _normalize_front_heading_text(line_text)
    canonical = FRONT_HEADING_ALIASES.get(normalized)
    if canonical == "Table of Contents" and isinstance(line_index, int):
        if line_index >= max(int(toc_heading_max_line or 40), 1):
            return None
    return canonical


def repair_front_matter_coverage(front_nodes, first_section_page):
    """
    计算 front matter 覆盖情况。
    返回:
    {
      "flat_front_nodes": [...],
      "need_root": bool,
      "front_end_page": int,
      "gap_pages": [...]
    }
    """
    if not isinstance(first_section_page, int) or first_section_page <= 1:
        return {
            "flat_front_nodes": [],
            "need_root": False,
            "front_end_page": 0,
            "gap_pages": [],
        }

    front_end_page = first_section_page - 1
    normalized_front_nodes = []
    for node in front_nodes or []:
        if not isinstance(node, dict):
            continue
        copied = dict(node)
        copied["retrieval_disabled"] = True
        normalized_front_nodes.append(copied)

    target_pages = set(range(1, front_end_page + 1))
    covered_pages = set()
    for node in normalized_front_nodes:
        start_page = convert_physical_index_to_int(node.get("start_index"))
        end_page = convert_physical_index_to_int(node.get("end_index"))
        if not isinstance(start_page, int) or not isinstance(end_page, int):
            continue
        if start_page > end_page:
            start_page, end_page = end_page, start_page
        start_page = max(1, start_page)
        end_page = min(front_end_page, end_page)
        if end_page < start_page:
            continue
        covered_pages.update(range(start_page, end_page + 1))

    gap_pages = sorted(target_pages - covered_pages)
    need_root = (len(normalized_front_nodes) == 0) or bool(gap_pages)
    return {
        "flat_front_nodes": normalized_front_nodes,
        "need_root": need_root,
        "front_end_page": front_end_page,
        "gap_pages": gap_pages,
    }


def ensure_front_matter_root(toc_tree, first_section_page, need_root):
    """
    当 front matter 存在空洞时，把 front 节点挂到 Front Matter 根节点下，
    并保证 1..first_section_page-1 被覆盖。
    """
    if not need_root or not isinstance(first_section_page, int) or first_section_page <= 1:
        return toc_tree

    front_end_page = first_section_page - 1
    front_title_set = {
        "abstract",
        "status of this memo",
        "copyright notice",
        "table of contents",
    }

    remaining_roots = []
    front_children = []
    for node in toc_tree or []:
        start_index = node.get("start_index")
        raw_title = str(node.get("raw_title") or node.get("title") or "").strip().lower()
        is_front_like = (
            node.get("structure") is None
            and isinstance(start_index, int)
            and 1 <= start_index <= front_end_page
            and (node.get("retrieval_disabled") is True or raw_title in front_title_set)
        )
        if is_front_like:
            front_children.append(node)
        else:
            remaining_roots.append(node)

    sort_nodes_by_position(front_children)
    front_root = {
        "title": "Front Matter",
        "display_title": "Front Matter",
        "raw_title": "Front Matter",
        "full_title": "Front Matter",
        "structure": None,
        "start_index": 1,
        "end_index": front_end_page,
        "retrieval_disabled": True,
    }
    if front_children:
        front_root["nodes"] = front_children

    return [front_root] + remaining_roots


def extract_front_matter_rules(page_list, first_section_structure, first_section_title, first_section_page, logger=None):
    """
    规则提取 front matter（含 TOC 跨页续接）：
    - 节点字段: start_index/end_index + start_line/end_line
    - TOC 可跨页，且可在正文同页按 introduction 行截断
    """

    if not isinstance(first_section_page, int) or first_section_page <= 1:
        return []
    if first_section_page > len(page_list):
        first_section_page = len(page_list)
    if first_section_page <= 0:
        return []

    hits = []
    for page_num in range(1, first_section_page + 1):
        lines = str(page_list[page_num - 1][0] or "").splitlines()
        for line_idx, line in enumerate(lines):
            heading = _canonical_front_heading(line, line_idx, toc_heading_max_line=40)
            if not heading:
                continue
            hits.append(
                {
                    "title": heading,
                    "page": page_num,
                    "line": line_idx,
                }
            )

    if not hits:
        return []

    intro_line = None
    if first_section_structure:
        intro_line = find_section_heading_line(
            page_text=page_list[first_section_page - 1][0],
            structure=first_section_structure,
            title=first_section_title,
        )
        if isinstance(intro_line, int):
            hits.append({"title": "__STOP__", "page": first_section_page, "line": intro_line})

    hits.sort(key=lambda x: (x["page"], x["line"], x["title"]))

    # 同一标题只保留首次命中。
    ordered_hits = []
    seen_titles = set()
    for hit in hits:
        title = hit["title"]
        if title == "__STOP__":
            ordered_hits.append(hit)
            continue
        if title in seen_titles:
            continue
        seen_titles.add(title)
        ordered_hits.append(hit)

    front_nodes = []
    for idx, hit in enumerate(ordered_hits):
        title = hit["title"]
        if title == "__STOP__":
            continue

        page_num = hit["page"]
        page_lines = str(page_list[page_num - 1][0] or "").splitlines()

        node = {
            "title": title,
            "node_id": FRONT_NODE_ID_MAP.get(title, f"front_{page_num:03d}_{hit['line']:04d}"),
            "structure": None,
            "physical_index": page_num,
            "start_index": page_num,
            "end_index": page_num,
            "start_line": hit["line"],
            "end_line": max(hit["line"], len(page_lines) - 1),
            "retrieval_disabled": True,
        }

        next_real_hit = None
        for j in range(idx + 1, len(ordered_hits)):
            candidate = ordered_hits[j]
            if candidate["title"] != "__STOP__":
                next_real_hit = candidate
                break

        if next_real_hit and next_real_hit["page"] == page_num:
            node["end_line"] = max(hit["line"], next_real_hit["line"] - 1)

        if title == "Table of Contents":
            toc_end_page = page_num
            while toc_end_page + 1 <= first_section_page:
                next_text = str(page_list[toc_end_page][0] or "")
                if is_toc_continuation_page(next_text):
                    toc_end_page += 1
                else:
                    break
            node["end_index"] = toc_end_page
            node["toc_span"] = True
            node["retrieval_disabled"] = True

            end_page_lines = str(page_list[toc_end_page - 1][0] or "").splitlines()
            node["end_line"] = max(0, len(end_page_lines) - 1)
            if toc_end_page == first_section_page and isinstance(intro_line, int):
                node["end_line"] = intro_line - 1

        front_nodes.append(node)

    front_nodes.sort(key=lambda x: (x.get("start_index", 10**9), x.get("start_line", 0), str(x.get("node_id", ""))))
    if logger:
        logger.info(
            {
                "front_matter_spans": [
                    (
                        n.get("title"),
                        n.get("start_index"),
                        n.get("end_index"),
                        n.get("start_line"),
                        n.get("end_line"),
                        n.get("node_id"),
                    )
                    for n in front_nodes
                ],
                "first_section_anchor": {
                    "page": first_section_page,
                    "structure": first_section_structure,
                    "title": first_section_title,
                    "intro_line": intro_line,
                },
            }
        )
    return front_nodes


def check_toc(page_list, opt=None):
    toc_page_list = find_toc_pages(start_page_index=0, page_list=page_list, opt=opt)
    if len(toc_page_list) == 0:
        print('no toc found')
        return {'toc_content': None, 'toc_page_list': [], 'page_index_given_in_toc': 'no'}
    else:
        print('toc found')
        toc_json = toc_extractor(page_list, toc_page_list, opt.model)

        if toc_json['page_index_given_in_toc'] == 'yes':
            print('index found')
            return {'toc_content': toc_json['toc_content'], 'toc_page_list': toc_page_list, 'page_index_given_in_toc': 'yes'}
        else:
            current_start_index = toc_page_list[-1] + 1
            
            while (toc_json['page_index_given_in_toc'] == 'no' and 
                   current_start_index < len(page_list) and 
                   current_start_index < opt.toc_check_page_num):
                
                additional_toc_pages = find_toc_pages(
                    start_page_index=current_start_index,
                    page_list=page_list,
                    opt=opt
                )
                
                if len(additional_toc_pages) == 0:
                    break

                additional_toc_json = toc_extractor(page_list, additional_toc_pages, opt.model)
                if additional_toc_json['page_index_given_in_toc'] == 'yes':
                    print('index found')
                    return {'toc_content': additional_toc_json['toc_content'], 'toc_page_list': additional_toc_pages, 'page_index_given_in_toc': 'yes'}

                else:
                    current_start_index = additional_toc_pages[-1] + 1
            print('index not found')
            return {'toc_content': toc_json['toc_content'], 'toc_page_list': toc_page_list, 'page_index_given_in_toc': 'no'}






################### fix incorrect toc #########################################################
def single_toc_item_index_fixer(section_title, content, model="gpt-4o-2024-11-20"):
    toc_extractor_prompt = """
    You are given a section title and several pages of a document, your job is to find the physical index of the start page of the section in the partial document.

    The provided pages contains tags like <physical_index_X> and <physical_index_X> to indicate the physical location of the page X.

    Reply in a JSON format:
    {
        "thinking": <explain which page, started and closed by <physical_index_X>, contains the start of this section>,
        "physical_index": "<physical_index_X>" (keep the format)
    }
    Directly return the final JSON structure. Do not output anything else."""

    prompt = toc_extractor_prompt + '\nSection Title:\n' + str(section_title) + '\nDocument pages:\n' + content
    response = ChatGPT_API(model=model, prompt=prompt)
    json_content = extract_json(response)    
    return convert_physical_index_to_int(json_content.get('physical_index'))



async def fix_incorrect_toc(toc_with_page_number, page_list, incorrect_results, start_index=1, model=None, logger=None):
    print(f'start fix_incorrect_toc with {len(incorrect_results)} incorrect results')
    incorrect_indices = {result['list_index'] for result in incorrect_results}
    
    end_index = len(page_list) + start_index - 1
    
    incorrect_results_and_range_logs = []
    # Helper function to process and check a single incorrect item
    async def process_and_check_item(incorrect_item):
        item_idx = incorrect_item['list_index']
        
        # Check if list_index is valid
        if item_idx < 0 or item_idx >= len(toc_with_page_number):
            # Return an invalid result for out-of-bounds indices
            return {
                'list_index': item_idx,
                'title': incorrect_item['title'],
                'physical_index': incorrect_item.get('physical_index'),
                'is_valid': False
            }
        
        # Find the previous correct item
        prev_correct = None
        for i in range(item_idx-1, -1, -1):
            if i not in incorrect_indices and i >= 0 and i < len(toc_with_page_number):
                physical_index = toc_with_page_number[i].get('physical_index')
                if physical_index is not None:
                    prev_correct = physical_index
                    break
        # If no previous correct item found, use start_index
        if prev_correct is None:
            prev_correct = start_index - 1
        
        # Find the next correct item
        next_correct = None
        for i in range(item_idx+1, len(toc_with_page_number)):
            if i not in incorrect_indices and i >= 0 and i < len(toc_with_page_number):
                physical_index = toc_with_page_number[i].get('physical_index')
                if physical_index is not None:
                    next_correct = physical_index
                    break
        # If no next correct item found, use end_index
        if next_correct is None:
            next_correct = end_index
        
        incorrect_results_and_range_logs.append({
            'list_index': item_idx,
            'title': incorrect_item['title'],
            'prev_correct': prev_correct,
            'next_correct': next_correct
        })

        page_contents=[]
        for page_index in range(prev_correct, next_correct+1):
            # Add bounds checking to prevent IndexError
            page_idx = page_index - start_index
            if page_idx >= 0 and page_idx < len(page_list):
                page_text = f"<physical_index_{page_index}>\n{page_list[page_idx][0]}\n<physical_index_{page_index}>\n\n"
                page_contents.append(page_text)
            else:
                continue
        content_range = ''.join(page_contents)
        
        physical_index_int = single_toc_item_index_fixer(incorrect_item['title'], content_range, model)
        
        # Check if the result is correct
        check_item = incorrect_item.copy()
        check_item['physical_index'] = physical_index_int
        check_result = await check_title_appearance(check_item, page_list, start_index, model)

        return {
            'list_index': item_idx,
            'title': incorrect_item['title'],
            'physical_index': physical_index_int,
            'is_valid': check_result['answer'] == 'yes'
        }

    # Process incorrect items concurrently
    tasks = [
        process_and_check_item(item)
        for item in incorrect_results
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for item, result in zip(incorrect_results, results):
        if isinstance(result, Exception):
            print(f"Processing item {item} generated an exception: {result}")
            continue
    results = [result for result in results if not isinstance(result, Exception)]

    # Update the toc_with_page_number with the fixed indices and check for any invalid results
    invalid_results = []
    for result in results:
        if result['is_valid']:
            # Add bounds checking to prevent IndexError
            item_idx = result['list_index']
            if 0 <= item_idx < len(toc_with_page_number):
                toc_with_page_number[item_idx]['physical_index'] = result['physical_index']
            else:
                # Index is out of bounds, treat as invalid
                invalid_results.append({
                    'list_index': result['list_index'],
                    'title': result['title'],
                    'physical_index': result['physical_index'],
                })
        else:
            invalid_results.append({
                'list_index': result['list_index'],
                'title': result['title'],
                'physical_index': result['physical_index'],
            })

    logger.info(f'incorrect_results_and_range_logs: {incorrect_results_and_range_logs}')
    logger.info(f'invalid_results: {invalid_results}')

    return toc_with_page_number, invalid_results



async def fix_incorrect_toc_with_retries(toc_with_page_number, page_list, incorrect_results, start_index=1, max_attempts=3, model=None, logger=None):
    print('start fix_incorrect_toc')
    fix_attempt = 0
    current_toc = toc_with_page_number
    current_incorrect = incorrect_results

    while current_incorrect:
        print(f"Fixing {len(current_incorrect)} incorrect results")
        
        current_toc, current_incorrect = await fix_incorrect_toc(current_toc, page_list, current_incorrect, start_index, model, logger)
                
        fix_attempt += 1
        if fix_attempt >= max_attempts:
            logger.info("Maximum fix attempts reached")
            break
    
    return current_toc, current_incorrect




################### verify toc #########################################################
async def verify_toc(page_list, list_result, start_index=1, N=None, model=None):
    print('start verify_toc')
    # Find the last non-None physical_index
    last_physical_index = None
    for item in reversed(list_result):
        if item.get('physical_index') is not None:
            last_physical_index = item['physical_index']
            break
    
    # Early return if we don't have valid physical indices
    if last_physical_index is None or last_physical_index < len(page_list)/2:
        return 0, []
    
    # Determine which items to check
    if N is None:
        print('check all items')
        sample_indices = range(0, len(list_result))
    else:
        N = min(N, len(list_result))
        print(f'check {N} items')
        sample_indices = random.sample(range(0, len(list_result)), N)

    # Prepare items with their list indices
    indexed_sample_list = []
    for idx in sample_indices:
        item = list_result[idx]
        # Skip items with None physical_index (these were invalidated by validate_and_truncate_physical_indices)
        if item.get('physical_index') is not None:
            item_with_index = item.copy()
            item_with_index['list_index'] = idx  # Add the original index in list_result
            indexed_sample_list.append(item_with_index)

    # Run checks concurrently
    tasks = [
        check_title_appearance(item, page_list, start_index, model)
        for item in indexed_sample_list
    ]
    results = await asyncio.gather(*tasks)
    
    # Process results
    correct_count = 0
    incorrect_results = []
    for result in results:
        if result['answer'] == 'yes':
            correct_count += 1
        else:
            incorrect_results.append(result)
    
    # Calculate accuracy
    checked_count = len(results)
    accuracy = correct_count / checked_count if checked_count > 0 else 0
    print(f"accuracy: {accuracy*100:.2f}%")
    return accuracy, incorrect_results





################### main process #########################################################
async def meta_processor(page_list, mode=None, toc_content=None, toc_page_list=None, start_index=1, opt=None, logger=None):
    print(mode)
    print(f'start_index: {start_index}')
    
    if mode == 'process_toc_with_page_numbers':
        toc_with_page_number = process_toc_with_page_numbers(toc_content, toc_page_list, page_list, toc_check_page_num=opt.toc_check_page_num, model=opt.model, logger=logger)
    elif mode == 'process_toc_no_page_numbers':
        toc_with_page_number = process_toc_no_page_numbers(toc_content, toc_page_list, page_list, model=opt.model, logger=logger)
    else:
        toc_with_page_number = process_no_toc(page_list, start_index=start_index, model=opt.model, logger=logger)
            
    toc_with_page_number = [item for item in toc_with_page_number if item.get('physical_index') is not None] 
    
    toc_with_page_number = validate_and_truncate_physical_indices(
        toc_with_page_number, 
        len(page_list), 
        start_index=start_index, 
        logger=logger
    )
    
    accuracy, incorrect_results = await verify_toc(page_list, toc_with_page_number, start_index=start_index, model=opt.model)
        
    logger.info({
        'mode': 'process_toc_with_page_numbers',
        'accuracy': accuracy,
        'incorrect_results': incorrect_results
    })
    if accuracy == 1.0 and len(incorrect_results) == 0:
        return toc_with_page_number
    if accuracy > 0.6 and len(incorrect_results) > 0:
        toc_with_page_number, incorrect_results = await fix_incorrect_toc_with_retries(toc_with_page_number, page_list, incorrect_results,start_index=start_index, max_attempts=3, model=opt.model, logger=logger)
        return toc_with_page_number
    else:
        if mode == 'process_toc_with_page_numbers':
            return await meta_processor(page_list, mode='process_toc_no_page_numbers', toc_content=toc_content, toc_page_list=toc_page_list, start_index=start_index, opt=opt, logger=logger)
        elif mode == 'process_toc_no_page_numbers':
            return await meta_processor(page_list, mode='process_no_toc', start_index=start_index, opt=opt, logger=logger)
        else:
            raise Exception('Processing failed')
        
 
async def process_large_node_recursively(node, page_list, opt=None, logger=None):
    # 1. 计算 Token，判断是否需要细分
    node_page_list = page_list[node['start_index']-1:node['end_index']]
    token_num = sum([page[1] for page in node_page_list])

    page_span = max(0, node['end_index'] - node['start_index'] + 1)
    node_structure = str(node.get("structure") or "").strip()
    node_depth = node_structure.count(".") + 1 if node_structure else 1
    is_large_node = (
        node['end_index'] - node['start_index'] > opt.max_page_num_each_node
        or token_num >= opt.max_token_num_each_node
    )
    should_probe = (node_depth < 5) or is_large_node

    if should_probe and node_page_list:
        if is_large_node and logger:
            logger.info(f"Refining large node (Adaptive): {node['title']} ({node['start_index']}-{node['end_index']})")

        # --- 步骤 A: 轻量 heading 规则抽取 ---
        parent_structure = node.get("structure")
        if not parent_structure:
            title_match = re.match(r"^\s*(\d+(?:\.\d+)*)\b", str(node.get("title") or ""))
            if title_match:
                parent_structure = title_match.group(1)

        if page_span <= max(int(getattr(opt, "max_page_num_each_node", 1) or 1), 1):
            scan_lines = 0
        else:
            scan_lines = int(getattr(opt, "heading_scan_lines", 250) or 250)

        raw_sub_toc = extract_sub_toc_by_headings(
            node_page_list=node_page_list,
            start_index=node['start_index'],
            parent_structure=parent_structure,
            max_lines_per_page=scan_lines,
        )

        unique_pages = len(
            {
                item.get("physical_index")
                for item in raw_sub_toc
                if isinstance(item.get("physical_index"), int)
            }
        )

        # 仅大节点且规则抽取失败时，回退到 LLM 全文抽取
        if is_large_node and (len(raw_sub_toc) < 3 or unique_pages <= 1):
            if logger:
                logger.info(
                    f"Heading extract fallback to process_no_toc: "
                    f"count={len(raw_sub_toc)}, unique_pages={unique_pages}, "
                    f"range=({node['start_index']}-{node['end_index']})"
                )
            raw_sub_toc = process_no_toc(node_page_list, start_index=node['start_index'], model=opt.model, logger=logger)

        if not raw_sub_toc:
            return node

        # --- 步骤 B: 清洗 (Filtering) ---
        valid_candidates = []
        for item in raw_sub_toc:
            check = await check_title_appearance_fast(item, page_list, start_index=1)
            if check['answer'] == 'yes':
                valid_candidates.append(item)
            else:
                if logger:
                    logger.info(f"Filtered hallucination: {item['title']}")

        if not valid_candidates:
            return node

        # --- 步骤 C: 起始校验 ---
        checked_sub_toc = await check_title_appearance_in_start_concurrent(
            valid_candidates,
            page_list,
            model=opt.model,
            logger=logger
        )

        if not checked_sub_toc:
            return node

        # 去掉与父节点同结构的自引用项，避免重复递归
        parent_structure_norm = str(parent_structure).strip() if parent_structure else None
        if parent_structure_norm:
            pruned = []
            for item in checked_sub_toc:
                child_structure = item.get("structure")
                child_structure_norm = str(child_structure).strip() if child_structure is not None else None
                if child_structure_norm and child_structure_norm == parent_structure_norm:
                    continue
                pruned.append(item)
            checked_sub_toc = pruned

        if not checked_sub_toc:
            return node

        # --- 步骤 D: 后处理与挂载 ---
        if node['title'].strip() == checked_sub_toc[0]['title'].strip():
            if len(checked_sub_toc) > 1:
                node['nodes'] = post_processing(checked_sub_toc[1:], node['end_index'])
                node['end_index'] = checked_sub_toc[1]['start_index']
            else:
                node['nodes'] = []
        else:
            node['nodes'] = post_processing(checked_sub_toc, node['end_index'])
            node['end_index'] = checked_sub_toc[0]['start_index'] if checked_sub_toc else node['end_index']
        
    # --- 递归下一层 ---
    if 'nodes' in node and node['nodes']:
        tasks = [
            process_large_node_recursively(child_node, page_list, opt, logger=logger)
            for child_node in node['nodes']
        ]
        await asyncio.gather(*tasks)
    
    return node

async def check_title_appearance_fast(item, page_list, start_index=1):
    """
    快速验证标题是否在页面中（纯本地逻辑，无 LLM）。
    用于在调用昂贵的 check_title_appearance_in_start 之前过滤幻觉。
    """
    title = item['title'].strip()
    structure = item.get('structure')  # 例如 "4.2.1"
    
    # 1. 物理页码越界检查
    if 'physical_index' not in item or item['physical_index'] is None:
        return {'answer': 'no', 'reason': 'no_index'}
    
    page_number = item['physical_index']
    real_idx = page_number - start_index
    if real_idx < 0 or real_idx >= len(page_list):
        return {'answer': 'no', 'reason': 'out_of_bound'}

    page_text = page_list[real_idx][0]
    
    # 2. 强规则匹配 (正则匹配章节号 + 标题)
    # 针对 FC-LS 这种规范文档，这是最准的
    if structure:
        safe_structure = re.escape(str(structure))
        safe_title = re.escape(title)
        
        # 匹配 "4.2.1 Description" 或 "4.2.1. Description"
        pattern_str = rf"{safe_structure}\.?\s*{safe_title}"
        if re.search(pattern_str, page_text, re.IGNORECASE):
            return {'answer': 'yes'}
            
        # 如果章节号很长 (如 4.2.10.1)，光匹配章节号就足够可信
        if len(str(structure).split('.')) >= 3:
            if re.search(rf"(^|\n)\s*{safe_structure}\.?\s", page_text):
                 return {'answer': 'yes'}

    # 3. 模糊匹配 (Fuzzy Match)
    # 仅在页面前 50% 区域搜索，防止匹配到末尾的 Reference
    scan_limit = int(len(page_text) * 0.5) + 500
    header_text = page_text[:scan_limit]
    
    # 只要相似度超过 85%，我们就认为它存在，才有资格进入下一步的 LLM 起始检测
    score = fuzz.partial_ratio(title.lower(), header_text.lower())
    if score >= 85:
        return {'answer': 'yes'}
    
    return {'answer': 'no', 'reason': 'fuzzy_low'}

async def tree_parser(page_list, opt, doc=None, logger=None):
    bookmark_toc = get_pdf_bookmarks_toc(doc)
    use_bookmark_toc = False
    if bookmark_toc:
        toc_with_page_number = bookmark_toc
        use_bookmark_toc = True
        if logger:
            logger.info(
                {
                    "toc_source": "pdf_bookmarks",
                    "bookmark_items": len(bookmark_toc),
                    "bookmark_preview": bookmark_toc[:8],
                }
            )
    else:
        check_toc_result = check_toc(page_list, opt)
        logger.info(check_toc_result)

        if check_toc_result.get("toc_content") and check_toc_result["toc_content"].strip() and check_toc_result["page_index_given_in_toc"] == "yes":
            toc_with_page_number = await meta_processor(
                page_list, 
                mode='process_toc_with_page_numbers', 
                start_index=1, 
                toc_content=check_toc_result['toc_content'], 
                toc_page_list=check_toc_result['toc_page_list'], 
                opt=opt,
                logger=logger)
        else:
            toc_with_page_number = await meta_processor(
                page_list, 
                mode='process_no_toc', 
                start_index=1, 
                opt=opt,
                logger=logger)

    # Front Matter 规则提取（替代旧 Preface 大口袋）
    first_section_info = _get_first_body_section_info(toc_with_page_number)
    first_section_page = first_section_info["first_section_page"]
    first_section_structure = first_section_info["first_section_structure"]
    first_section_title = first_section_info["first_section_title"]
    front_coverage_plan = {
        "flat_front_nodes": [],
        "need_root": False,
        "front_end_page": 0,
        "gap_pages": [],
    }

    if logger:
        logger.info({"first_section_info": first_section_info})

    if first_section_page > 1:
        extracted_front_nodes = extract_front_matter_rules(
            page_list=page_list,
            first_section_structure=first_section_structure,
            first_section_title=first_section_title,
            first_section_page=first_section_page,
            logger=logger,
        )
        front_coverage_plan = repair_front_matter_coverage(
            extracted_front_nodes,
            first_section_page=first_section_page,
        )

        if logger:
            logger.info(
                {
                    "front_matter_coverage_repair": {
                        "front_end_page": front_coverage_plan["front_end_page"],
                        "need_root": front_coverage_plan["need_root"],
                        "gap_pages": front_coverage_plan["gap_pages"],
                        "front_nodes_count": len(front_coverage_plan["flat_front_nodes"]),
                    }
                }
            )

        toc_with_page_number = front_coverage_plan["flat_front_nodes"] + toc_with_page_number
    elif logger:
        logger.info({"skip_front_matter_extraction": True, "first_section_info": first_section_info})

    toc_with_page_number = await check_title_appearance_in_start_concurrent(toc_with_page_number, page_list, model=opt.model, logger=logger)
    
    # Filter out items with None physical_index before post_processings
    valid_toc_items = [item for item in toc_with_page_number if item.get('physical_index') is not None]
    
    toc_tree = post_processing(valid_toc_items, len(page_list))
    if not use_bookmark_toc:
        tasks = [
            process_large_node_recursively(node, page_list, opt, logger=logger)
            for node in toc_tree
        ]
        await asyncio.gather(*tasks)
    elif logger:
        logger.info({"skip_recursive_refine": True, "reason": "bookmark_toc_already_hierarchical"})

    # 若 front matter 区间存在空洞，强制挂一个 Front Matter 根节点覆盖 1..first_section_page-1。
    toc_tree = ensure_front_matter_root(
        toc_tree=toc_tree,
        first_section_page=first_section_page,
        need_root=front_coverage_plan["need_root"],
    )

    # 输出顺序稳定：roots + children 按 (start_index, start_line, node_id/title) 排序。
    sort_nodes_by_position(toc_tree)
    
    return toc_tree


def page_index_main(doc, opt=None):
    logger = JsonLogger(doc)
    
    is_valid_pdf = (
        (isinstance(doc, str) and os.path.isfile(doc) and doc.lower().endswith(".pdf")) or 
        isinstance(doc, BytesIO)
    )
    if not is_valid_pdf:
        raise ValueError("Unsupported input type. Expected a PDF file path or BytesIO object.")

    print('Parsing PDF...')
    page_list = get_page_tokens(doc)

    logger.info({'total_page_number': len(page_list)})
    logger.info({'total_token': sum([page[1] for page in page_list])})

    def _log_front_matter_debug(structure):
        nodes = structure_to_list(structure or [])
        normalized_targets = {
            "abstract",
            "status of this memo",
            "copyright notice",
            "table of contents",
        }

        front_nodes = []
        for node in nodes:
            title = str(node.get("raw_title") or node.get("title") or "").strip()
            if title.lower() in normalized_targets:
                front_nodes.append(node)

        front_nodes.sort(
            key=lambda n: (
                n.get("start_index") if isinstance(n.get("start_index"), int) else 10**9,
                n.get("start_line") if isinstance(n.get("start_line"), int) else 0,
                str(n.get("node_id") or n.get("title") or ""),
            )
        )

        if not front_nodes:
            return

        span_rows = []
        preview_rows = {}
        for node in front_nodes:
            title = str(node.get("raw_title") or node.get("title") or "")
            span_rows.append(
                (
                    title,
                    node.get("start_index"),
                    node.get("end_index"),
                    node.get("start_line"),
                    node.get("end_line"),
                    node.get("node_id"),
                )
            )

            text_lines = str(node.get("text") or "").splitlines()
            preview_rows[title] = text_lines[:2]

        logger.info({"front_matter_spans_debug": span_rows})
        logger.info({"front_matter_text_preview_2lines": preview_rows})

        by_title = {
            str(node.get("raw_title") or node.get("title") or "").strip().lower(): str(node.get("text") or "")
            for node in front_nodes
        }
        toc_text = by_title.get("table of contents", "").lower()
        checks = {
            "front_order_abstract_status_copyright": [
                str(node.get("raw_title") or node.get("title") or "")
                for node in front_nodes
                if str(node.get("raw_title") or node.get("title") or "").strip().lower()
                in {"abstract", "status of this memo", "copyright notice"}
            ][:3]
            == ["Abstract", "Status of This Memo", "Copyright Notice"],
            "abstract_not_contains_status": "status of this memo" not in by_title.get("abstract", "").lower(),
            "status_not_contains_toc": "table of contents" not in by_title.get("status of this memo", "").lower(),
            "copyright_not_contains_abstract": "abstract" not in by_title.get("copyright notice", "").lower(),
            "toc_contains_continuation_markers": ("7." in toc_text) or ("appendix" in toc_text),
            "toc_not_contains_intro_paragraph": "an increasingly important feature" not in toc_text,
        }
        logger.info({"front_matter_assertions": checks})

    async def page_index_builder():
        structure = await tree_parser(page_list, opt, doc=doc, logger=logger)
        if opt.if_add_node_id == 'yes':
            write_node_id(structure)    
        if opt.if_add_node_text == 'yes':
            add_node_text(structure, page_list)
            _log_front_matter_debug(structure)
        if opt.if_add_node_summary == 'yes':
            if opt.if_add_node_text == 'no':
                add_node_text(structure, page_list)
                _log_front_matter_debug(structure)
            await generate_summaries_for_structure(structure, model=opt.model)
            if opt.if_add_node_text == 'no':
                remove_structure_text(structure)
            if opt.if_add_doc_description == 'yes':
                # Create a clean structure without unnecessary fields for description generation
                clean_structure = create_clean_structure_for_description(structure)
                doc_description = generate_doc_description(clean_structure, model=opt.model)
                return {
                    'doc_name': get_pdf_name(doc),
                    'doc_description': doc_description,
                    'structure': structure,
                }
        return {
            'doc_name': get_pdf_name(doc),
            'structure': structure,
        }

    return asyncio.run(page_index_builder())


def page_index(doc, model=None, toc_check_page_num=None, max_page_num_each_node=None, max_token_num_each_node=None,
               if_add_node_id=None, if_add_node_summary=None, if_add_doc_description=None, if_add_node_text=None):
    
    user_opt = {
        arg: value for arg, value in locals().items()
        if arg != "doc" and value is not None
    }
    opt = ConfigLoader().load(user_opt)
    return page_index_main(doc, opt)


def validate_and_truncate_physical_indices(toc_with_page_number, page_list_length, start_index=1, logger=None):
    """
    Validates and truncates physical indices that exceed the actual document length.
    This prevents errors when TOC references pages that don't exist in the document (e.g. the file is broken or incomplete).
    """
    if not toc_with_page_number:
        return toc_with_page_number
    
    max_allowed_page = page_list_length + start_index - 1
    truncated_items = []
    
    for i, item in enumerate(toc_with_page_number):
        if item.get('physical_index') is not None:
            original_index = item['physical_index']
            if original_index > max_allowed_page:
                item['physical_index'] = None
                truncated_items.append({
                    'title': item.get('title', 'Unknown'),
                    'original_index': original_index
                })
                if logger:
                    logger.info(f"Removed physical_index for '{item.get('title', 'Unknown')}' (was {original_index}, too far beyond document)")
    
    if truncated_items and logger:
        logger.info(f"Total removed items: {len(truncated_items)}")
        
    print(f"Document validation: {page_list_length} pages, max allowed index: {max_allowed_page}")
    if truncated_items:
        print(f"Truncated {len(truncated_items)} TOC items that exceeded document length")
     
    return toc_with_page_number


def _tokenize_for_retrieval(text):
    text = str(text or "")
    lower_text = text.lower()
    english_tokens = re.findall(r"[a-z0-9]{2,}", lower_text)
    cjk_tokens = re.findall(r"[\u4e00-\u9fff]", text)
    return set(english_tokens + cjk_tokens)


def _node_retrieval_text(node):
    parts = []
    for key in ("title", "summary", "prefix_summary", "text"):
        value = node.get(key)
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def _score_node_for_question(node, question):
    question_tokens = _tokenize_for_retrieval(question)
    title = str(node.get("title", ""))
    text = _node_retrieval_text(node)
    title_tokens = _tokenize_for_retrieval(title)
    text_tokens = _tokenize_for_retrieval(text[:12000])

    overlap_title = len(question_tokens.intersection(title_tokens))
    overlap_text = len(question_tokens.intersection(text_tokens))

    phrase_bonus = 0
    lower_question = question.lower().strip()
    if lower_question:
        if lower_question in title.lower():
            phrase_bonus += 3
        if lower_question in text.lower():
            phrase_bonus += 2

    return overlap_title * 2 + overlap_text + phrase_bonus




def answer_question_with_structure(structure, question, model=None, top_k=5):
    nodes = structure_to_list(structure or [])
    nodes_with_context = [node for node in nodes if _node_retrieval_text(node).strip()]
    if not nodes_with_context:
        return {
            "question": question,
            "answer": "No retrieval context is available in this tree JSON.",
            "used_sections": [],
        }

    scored = []
    for node in nodes_with_context:
        score = _score_node_for_question(node, question)
        scored.append((score, node))

    scored.sort(key=lambda item: item[0], reverse=True)
    top_k = max(1, int(top_k))
    selected = [item[1] for item in scored if item[0] > 0][:top_k]
    if not selected:
        selected = [item[1] for item in scored[:top_k]]

    context_chunks = []
    used_sections = []
    for idx, node in enumerate(selected, start=1):
        title = node.get("title", "")
        start_index = node.get("start_index")
        end_index = node.get("end_index")
        node_text = str(_node_retrieval_text(node))[:3000]
        context_chunks.append(
            f"[Section {idx}] title: {title}\n"
            f"pages: {start_index}-{end_index}\n"
            f"text:\n{node_text}"
        )
        used_sections.append(
            {
                "title": title,
                "start_index": start_index,
                "end_index": end_index,
                "has_text": bool(node.get("text")),
                "has_summary": bool(node.get("summary") or node.get("prefix_summary")),
            }
        )

    prompt = f"""
You are a document QA assistant.
Use only the provided context to answer the question.
If the context is insufficient, say you do not have enough evidence.

Question:
{question}

Context:
{chr(10).join(context_chunks)}

Return JSON:
{{
  "answer": "<concise answer in the same language as the question>"
}}
Do not output anything except JSON.
"""
    try:
        response = ChatGPT_API(model=model, prompt=prompt)
        payload = extract_json(response)
        answer = payload.get("answer", "").strip() if isinstance(payload, dict) else ""
        if not answer:
            answer = "Failed to parse a structured answer from the model response."
    except Exception:
        best_context = context_chunks[0] if context_chunks else ""
        answer = (
            "LLM is unavailable right now. Here is the top retrieved context section:\n\n"
            + best_context[:800]
        )

    return {
        "question": question,
        "answer": answer,
        "used_sections": used_sections,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build page index tree from a PDF.")
    parser.add_argument(
        "--input",
        dest="input_pdf",
        default=os.path.join("data", "raw", "FC-LS.pdf"),
        help="Path to input PDF file (default: data/raw/FC-LS.pdf)",
    )
    parser.add_argument(
        "--tree-json",
        dest="tree_json",
        default=None,
        help="Path to an existing PageIndex tree JSON file; skips PDF indexing when provided",
    )
    parser.add_argument(
        "--output",
        dest="output_json",
        default=None,
        help="Path to output JSON file (default: data/out/<pdf_name>_page_index.json)",
    )
    parser.add_argument("--model", dest="model", default=None, help="Override model name")
    parser.add_argument("--question", dest="question", default=None, help="Question to ask over the indexed document")
    parser.add_argument("--top-k", dest="top_k", type=int, default=5, help="Top K relevant sections for QA context")
    args = parser.parse_args()

    if args.tree_json:
        tree_json_path = args.tree_json
        if not os.path.isabs(tree_json_path):
            tree_json_path = os.path.join(os.path.dirname(__file__), tree_json_path)
        tree_json_path = os.path.abspath(tree_json_path)
        if not os.path.exists(tree_json_path):
            raise FileNotFoundError(f"Tree JSON not found: {tree_json_path}")

        if not args.question:
            raise ValueError("--question is required when using --tree-json")

        with open(tree_json_path, "r", encoding="utf-8") as f:
            tree_data = json.load(f)

        if isinstance(tree_data, dict) and "structure" in tree_data:
            structure = tree_data.get("structure", [])
        elif isinstance(tree_data, list):
            structure = tree_data
            tree_data = {"structure": structure}
        else:
            raise ValueError("Unsupported tree JSON format. Expected dict with 'structure' or a list of nodes.")

        qa_result = answer_question_with_structure(
            structure=structure,
            question=args.question,
            model=args.model,
            top_k=args.top_k,
        )
        tree_data["qa"] = qa_result
        print(f"Question: {qa_result['question']}")
        print(f"Answer: {qa_result['answer']}")

        if args.output_json:
            output_json = args.output_json
            if not os.path.isabs(output_json):
                output_json = os.path.join(os.path.dirname(__file__), output_json)
            output_json = os.path.abspath(output_json)
        else:
            tree_stem = os.path.splitext(os.path.basename(tree_json_path))[0]
            output_json = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "data", "out", f"{tree_stem}_qa.json")
            )

        os.makedirs(os.path.dirname(output_json), exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(tree_data, f, indent=2, ensure_ascii=False)
        print(f"Done. Output written to: {output_json}")
        raise SystemExit(0)

    input_pdf = args.input_pdf
    if not os.path.isabs(input_pdf):
        input_pdf = os.path.join(os.path.dirname(__file__), input_pdf)
    input_pdf = os.path.abspath(input_pdf)

    if not os.path.exists(input_pdf):
        raise FileNotFoundError(f"Input PDF not found: {input_pdf}")

    if args.output_json:
        output_json = args.output_json
        if not os.path.isabs(output_json):
            output_json = os.path.join(os.path.dirname(__file__), output_json)
        output_json = os.path.abspath(output_json)
    else:
        pdf_stem = os.path.splitext(os.path.basename(input_pdf))[0]
        output_json = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "data", "out", f"{pdf_stem}_page_index.json")
        )

    page_index_kwargs = {"model": args.model}
    if args.question:
        page_index_kwargs["if_add_node_text"] = "yes"
        page_index_kwargs["if_add_node_summary"] = "no"
        page_index_kwargs["if_add_doc_description"] = "no"

    try:
        result = page_index(input_pdf, **page_index_kwargs)
    except RuntimeError as e:
        raise RuntimeError(
            f"{e}\n"
            "Please check .env values: OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL_NAME, "
            "and ensure this host is reachable from your network."
        ) from e

    if args.question:
        try:
            qa_result = answer_question_with_structure(
                result.get("structure", []),
                question=args.question,
                model=args.model,
                top_k=args.top_k,
            )
            result["qa"] = qa_result
            print(f"Question: {qa_result['question']}")
            print(f"Answer: {qa_result['answer']}")
        except RuntimeError as e:
            raise RuntimeError(
                f"{e}\n"
                "QA step failed. Please verify OPENAI_BASE_URL connectivity and model availability."
            ) from e

    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Done. Output written to: {output_json}")
