import tiktoken
import openai
import logging
import os
from pathlib import Path
from datetime import datetime
import time
import json
import re
import PyPDF2
import copy
import asyncio
import pymupdf
from io import BytesIO
from dotenv import load_dotenv
import yaml
from types import SimpleNamespace as config
try:
    import anthropic
except Exception:
    anthropic = None

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("CHATGPT_API_KEY")
OPENAI_BASE_URL = (
    os.getenv("OPENAI_BASE_URL")
    or os.getenv("OPENAI_API_BASE_URL")
    or os.getenv("CHATGPT_BASE_URL")
)
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL") or os.getenv("CLAUDE_BASE_URL")
ANTHROPIC_MODEL_NAME = os.getenv("ANTHROPIC_MODEL_NAME") or os.getenv("CLAUDE_MODEL_NAME")
PROTOCOL_TWIN_LLM_PROVIDER = os.getenv("PROTOCOL_TWIN_LLM_PROVIDER", "openai")
PROTOCOL_TWIN_LLM_TIMEOUT_SEC = float(os.getenv("PROTOCOL_TWIN_LLM_TIMEOUT_SEC", "120"))
PROTOCOL_TWIN_LLM_MAX_RETRIES = int(os.getenv("PROTOCOL_TWIN_LLM_MAX_RETRIES", "3"))
PROTOCOL_TWIN_ANTHROPIC_MAX_TOKENS = int(os.getenv("PROTOCOL_TWIN_ANTHROPIC_MAX_TOKENS", "4096"))


def _build_openai_client(api_key=None, base_url=None, async_mode=False):
    client_kwargs = {}
    if api_key or OPENAI_API_KEY:
        client_kwargs["api_key"] = api_key or OPENAI_API_KEY
    if base_url or OPENAI_BASE_URL:
        client_kwargs["base_url"] = base_url or OPENAI_BASE_URL
    client_kwargs["timeout"] = PROTOCOL_TWIN_LLM_TIMEOUT_SEC
    if async_mode:
        return openai.AsyncOpenAI(**client_kwargs)
    return openai.OpenAI(**client_kwargs)


def _build_anthropic_client(api_key=None, base_url=None, async_mode=False):
    if anthropic is None:
        raise RuntimeError("anthropic package is not installed")

    client_kwargs = {"timeout": PROTOCOL_TWIN_LLM_TIMEOUT_SEC}
    if api_key or ANTHROPIC_API_KEY:
        client_kwargs["api_key"] = api_key or ANTHROPIC_API_KEY
    if base_url or ANTHROPIC_BASE_URL:
        client_kwargs["base_url"] = base_url or ANTHROPIC_BASE_URL
    if async_mode:
        return anthropic.AsyncAnthropic(**client_kwargs)
    return anthropic.Anthropic(**client_kwargs)


def _resolve_llm_provider(llm_provider=None):
    provider = (llm_provider or os.getenv("PROTOCOL_TWIN_LLM_PROVIDER") or PROTOCOL_TWIN_LLM_PROVIDER or "openai").strip().lower()
    if provider == "claude":
        provider = "anthropic"
    if provider not in {"openai", "anthropic"}:
        raise ValueError(f"Unsupported llm provider: {provider}. Expected one of: openai, anthropic")
    return provider


def _resolve_model_name(model, llm_provider=None):
    provider = _resolve_llm_provider(llm_provider)
    if model:
        return model

    if provider == "anthropic":
        resolved_model = ANTHROPIC_MODEL_NAME or OPENAI_MODEL_NAME
    else:
        resolved_model = OPENAI_MODEL_NAME

    if not resolved_model:
        if provider == "anthropic":
            raise ValueError("Model is not set. Please pass model or set ANTHROPIC_MODEL_NAME in .env.")
        raise ValueError("Model is not set. Please pass model or set OPENAI_MODEL_NAME in .env.")
    return resolved_model


def _normalize_chat_messages(prompt, chat_history=None):
    if chat_history:
        messages = list(chat_history)
        messages.append({"role": "user", "content": prompt})
        return messages
    return [{"role": "user", "content": prompt}]


def _split_anthropic_system_and_messages(messages):
    system_parts = []
    converted = []
    for message in messages or []:
        role = str(message.get("role") or "user").strip().lower()
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(str(part) for part in content)
        content = str(content or "")
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        converted.append({"role": role, "content": content})

    if not converted:
        converted = [{"role": "user", "content": ""}]

    system_prompt = "\n\n".join(part for part in system_parts if part).strip() or None
    return system_prompt, converted


def _extract_anthropic_text(response):
    text_parts = []
    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", None)
        block_text = getattr(block, "text", None)
        if block_type == "text" and block_text:
            text_parts.append(str(block_text))
    return "\n".join(text_parts).strip()

def count_tokens(text, model=None):
    if not text:
        return 0
    model_name = str(model or "").strip()
    try:
        if model_name:
            enc = tiktoken.encoding_for_model(model_name)
        else:
            enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        lowered = model_name.lower()
        if any(token in lowered for token in ["claude", "anthropic", "sonnet", "haiku", "opus"]):
            enc = tiktoken.get_encoding("cl100k_base")
        else:
            try:
                enc = tiktoken.get_encoding("o200k_base")
            except Exception:
                enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    return len(tokens)

def ChatGPT_API_with_finish_reason(model, prompt, api_key=None, base_url=None, chat_history=None, llm_provider=None):
    max_retries = PROTOCOL_TWIN_LLM_MAX_RETRIES
    llm_provider = _resolve_llm_provider(llm_provider)
    model = _resolve_model_name(model, llm_provider=llm_provider)
    last_error = None
    for i in range(max_retries):
        try:
            messages = _normalize_chat_messages(prompt, chat_history=chat_history)

            if llm_provider == "anthropic":
                client = _build_anthropic_client(api_key=api_key, base_url=base_url)
                system_prompt, anthropic_messages = _split_anthropic_system_and_messages(messages)
                kwargs = {
                    "model": model,
                    "messages": anthropic_messages,
                    "temperature": 0,
                    "max_tokens": PROTOCOL_TWIN_ANTHROPIC_MAX_TOKENS,
                }
                if system_prompt:
                    kwargs["system"] = system_prompt
                response = client.messages.create(**kwargs)
                finish_reason = "max_output_reached" if str(getattr(response, "stop_reason", "")).lower() == "max_tokens" else "finished"
                return _extract_anthropic_text(response), finish_reason

            client = _build_openai_client(api_key=api_key, base_url=base_url)
            response = client.chat.completions.create(model=model, messages=messages, temperature=0)
            finish_reason = "max_output_reached" if response.choices[0].finish_reason == "length" else "finished"
            return response.choices[0].message.content, finish_reason

        except Exception as e:
            last_error = e
            print('************* Retrying *************')
            logging.error(
                "Error [%s]: %r, cause=%r",
                type(e).__name__,
                e,
                getattr(e, "__cause__", None) or getattr(e, "__context__", None),
            )
            if i < max_retries - 1:
                time.sleep(1)  # Wait for 1秒 before retrying
            else:
                break
    raise RuntimeError(
        f"LLM request failed after {max_retries} retries. "
        f"provider={llm_provider}, model={model}, "
        f"base_url={base_url or (ANTHROPIC_BASE_URL if llm_provider == 'anthropic' else OPENAI_BASE_URL)}, "
        f"error={last_error}"
    )



def ChatGPT_API(model, prompt, api_key=None, base_url=None, chat_history=None, llm_provider=None):
    max_retries = PROTOCOL_TWIN_LLM_MAX_RETRIES
    llm_provider = _resolve_llm_provider(llm_provider)
    model = _resolve_model_name(model, llm_provider=llm_provider)
    last_error = None
    for i in range(max_retries):
        try:
            messages = _normalize_chat_messages(prompt, chat_history=chat_history)

            if llm_provider == "anthropic":
                client = _build_anthropic_client(api_key=api_key, base_url=base_url)
                system_prompt, anthropic_messages = _split_anthropic_system_and_messages(messages)
                kwargs = {
                    "model": model,
                    "messages": anthropic_messages,
                    "temperature": 0,
                    "max_tokens": PROTOCOL_TWIN_ANTHROPIC_MAX_TOKENS,
                }
                if system_prompt:
                    kwargs["system"] = system_prompt
                response = client.messages.create(**kwargs)
                return _extract_anthropic_text(response)

            client = _build_openai_client(api_key=api_key, base_url=base_url)
            response = client.chat.completions.create(model=model, messages=messages, temperature=0)
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            print('************* Retrying *************')
            logging.error(
                "Error [%s]: %r, cause=%r",
                type(e).__name__,
                e,
                getattr(e, "__cause__", None) or getattr(e, "__context__", None),
            )
            if i < max_retries - 1:
                time.sleep(1)  # Wait for 1秒 before retrying
            else:
                break
    raise RuntimeError(
        f"LLM request failed after {max_retries} retries. "
        f"provider={llm_provider}, model={model}, "
        f"base_url={base_url or (ANTHROPIC_BASE_URL if llm_provider == 'anthropic' else OPENAI_BASE_URL)}, "
        f"error={last_error}"
    )
            

async def ChatGPT_API_async(model, prompt, api_key=None, base_url=None, chat_history=None, llm_provider=None):
    max_retries = PROTOCOL_TWIN_LLM_MAX_RETRIES
    llm_provider = _resolve_llm_provider(llm_provider)
    model = _resolve_model_name(model, llm_provider=llm_provider)
    messages = _normalize_chat_messages(prompt, chat_history=chat_history)
    last_error = None
    for i in range(max_retries):
        try:
            if llm_provider == "anthropic":
                client = _build_anthropic_client(api_key=api_key, base_url=base_url, async_mode=True)
                system_prompt, anthropic_messages = _split_anthropic_system_and_messages(messages)
                kwargs = {
                    "model": model,
                    "messages": anthropic_messages,
                    "temperature": 0,
                    "max_tokens": PROTOCOL_TWIN_ANTHROPIC_MAX_TOKENS,
                }
                if system_prompt:
                    kwargs["system"] = system_prompt
                response = await client.messages.create(**kwargs)
                return _extract_anthropic_text(response)

            client = _build_openai_client(api_key=api_key, base_url=base_url, async_mode=True)
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            print('************* Retrying *************')
            logging.error(
                "Error [%s]: %r, cause=%r",
                type(e).__name__,
                e,
                getattr(e, "__cause__", None) or getattr(e, "__context__", None),
            )
            if i < max_retries - 1:
                await asyncio.sleep(1)  # Wait for 1s before retrying
            else:
                break
    raise RuntimeError(
        f"LLM request failed after {max_retries} retries. "
        f"provider={llm_provider}, model={model}, "
        f"base_url={base_url or (ANTHROPIC_BASE_URL if llm_provider == 'anthropic' else OPENAI_BASE_URL)}, "
        f"error={last_error}"
    )
            
            
def get_json_content(response):
    start_idx = response.find("```json")
    if start_idx != -1:
        start_idx += 7
        response = response[start_idx:]
        
    end_idx = response.rfind("```")
    if end_idx != -1:
        response = response[:end_idx]
    
    json_content = response.strip()
    return json_content
         

def extract_json(content):
    try:
        # First, try to extract JSON enclosed within ```json and ```
        start_idx = content.find("```json")
        if start_idx != -1:
            start_idx += 7  # Adjust index to start after the delimiter
            end_idx = content.rfind("```")
            json_content = content[start_idx:end_idx].strip()
        else:
            # If no delimiters, assume entire content could be JSON
            json_content = content.strip()

        # Clean up common issues that might cause parsing errors
        json_content = json_content.replace('None', 'null')  # Replace Python None with JSON null
        json_content = json_content.replace('\n', ' ').replace('\r', ' ')  # Remove newlines
        json_content = ' '.join(json_content.split())  # Normalize whitespace

        # Attempt to parse and return the JSON object
        return json.loads(json_content)
    except json.JSONDecodeError as e:
        logging.error(f"Failed to extract JSON: {e}")
        # Try to clean up the content further if initial parsing fails
        try:
            # Remove any trailing commas before closing brackets/braces
            json_content = json_content.replace(',]', ']').replace(',}', '}')
            return json.loads(json_content)
        except:
            logging.error("Failed to parse JSON even after cleanup")
            return {}
    except Exception as e:
        logging.error(f"Unexpected error while extracting JSON: {e}")
        return {}

def _reorder_node_id_key(data):
    if not isinstance(data, dict) or "node_id" not in data:
        return

    node_id_value = data.pop("node_id")
    ordered = {}
    inserted = False

    for key, value in data.items():
        ordered[key] = value
        if key == "full_title":
            ordered["node_id"] = node_id_value
            inserted = True

    if not inserted:
        ordered["node_id"] = node_id_value

    data.clear()
    data.update(ordered)


def write_node_id(data, node_id=0):
    if isinstance(data, dict):
        data['node_id'] = str(node_id).zfill(4)
        _reorder_node_id_key(data)
        node_id += 1
        for key in list(data.keys()):
            if 'nodes' in key:
                node_id = write_node_id(data[key], node_id)
    elif isinstance(data, list):
        for index in range(len(data)):
            node_id = write_node_id(data[index], node_id)
    return node_id

def get_nodes(structure):
    if isinstance(structure, dict):
        structure_node = copy.deepcopy(structure)
        structure_node.pop('nodes', None)
        nodes = [structure_node]
        for key in list(structure.keys()):
            if 'nodes' in key:
                nodes.extend(get_nodes(structure[key]))
        return nodes
    elif isinstance(structure, list):
        nodes = []
        for item in structure:
            nodes.extend(get_nodes(item))
        return nodes
    
def structure_to_list(structure):
    if isinstance(structure, dict):
        nodes = []
        nodes.append(structure)
        if 'nodes' in structure:
            nodes.extend(structure_to_list(structure['nodes']))
        return nodes
    elif isinstance(structure, list):
        nodes = []
        for item in structure:
            nodes.extend(structure_to_list(item))
        return nodes

    
def get_leaf_nodes(structure):
    if isinstance(structure, dict):
        if not structure['nodes']:
            structure_node = copy.deepcopy(structure)
            structure_node.pop('nodes', None)
            return [structure_node]
        else:
            leaf_nodes = []
            for key in list(structure.keys()):
                if 'nodes' in key:
                    leaf_nodes.extend(get_leaf_nodes(structure[key]))
            return leaf_nodes
    elif isinstance(structure, list):
        leaf_nodes = []
        for item in structure:
            leaf_nodes.extend(get_leaf_nodes(item))
        return leaf_nodes

def is_leaf_node(data, node_id):
    # Helper function to find the node by its node_id
    def find_node(data, node_id):
        if isinstance(data, dict):
            if data.get('node_id') == node_id:
                return data
            for key in data.keys():
                if 'nodes' in key:
                    result = find_node(data[key], node_id)
                    if result:
                        return result
        elif isinstance(data, list):
            for item in data:
                result = find_node(item, node_id)
                if result:
                    return result
        return None

    # Find the node with the given node_id
    node = find_node(data, node_id)

    # Check if the node is a leaf node
    if node and not node.get('nodes'):
        return True
    return False

def get_last_node(structure):
    return structure[-1]


def extract_text_from_pdf(pdf_path):
    pdf_reader = PyPDF2.PdfReader(pdf_path)
    ###return text not list 
    text=""
    for page_num in range(len(pdf_reader.pages)):
        page = pdf_reader.pages[page_num]
        text+=page.extract_text()
    return text

def get_pdf_title(pdf_path):
    pdf_reader = PyPDF2.PdfReader(pdf_path)
    meta = pdf_reader.metadata
    title = meta.title if meta and meta.title else 'Untitled'
    return title

def get_text_of_pages(pdf_path, start_page, end_page, tag=True):
    pdf_reader = PyPDF2.PdfReader(pdf_path)
    text = ""
    for page_num in range(start_page-1, end_page):
        page = pdf_reader.pages[page_num]
        page_text = page.extract_text()
        if tag:
            text += f"<start_index_{page_num+1}>\n{page_text}\n<end_index_{page_num+1}>\n"
        else:
            text += page_text
    return text

def get_first_start_page_from_text(text):
    start_page = -1
    start_page_match = re.search(r'<start_index_(\d+)>', text)
    if start_page_match:
        start_page = int(start_page_match.group(1))
    return start_page

def get_last_start_page_from_text(text):
    start_page = -1
    # Find all matches of start_index tags
    start_page_matches = re.finditer(r'<start_index_(\d+)>', text)
    # Convert iterator to list and get the last match if any exist
    matches_list = list(start_page_matches)
    if matches_list:
        start_page = int(matches_list[-1].group(1))
    return start_page


def sanitize_filename(filename, replacement='-'):
    # In Linux, only '/' and '\0' (null) are invalid in filenames.
    # Null can't be represented in strings, so we only handle '/'.
    return filename.replace('/', replacement)

def get_pdf_name(pdf_path):
    # Extract PDF name
    if isinstance(pdf_path, str):
        pdf_name = os.path.basename(pdf_path)
    elif isinstance(pdf_path, BytesIO):
        pdf_reader = PyPDF2.PdfReader(pdf_path)
        meta = pdf_reader.metadata
        pdf_name = meta.title if meta and meta.title else 'Untitled'
        pdf_name = sanitize_filename(pdf_name)
    return pdf_name


class JsonLogger:
    def __init__(self, file_path):
        # Extract PDF name for logger name
        pdf_name = get_pdf_name(file_path)
            
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filename = f"{pdf_name}_{current_time}.json"
        os.makedirs("./logs", exist_ok=True)
        # Initialize empty list to store all messages
        self.log_data = []

    def log(self, level, message, **kwargs):
        if isinstance(message, dict):
            self.log_data.append(message)
        else:
            self.log_data.append({'message': message})
        # Add new message to the log data
        
        # Write entire log data to file
        with open(self._filepath(), "w") as f:
            json.dump(self.log_data, f, indent=2)

    def info(self, message, **kwargs):
        self.log("INFO", message, **kwargs)

    def error(self, message, **kwargs):
        self.log("ERROR", message, **kwargs)

    def debug(self, message, **kwargs):
        self.log("DEBUG", message, **kwargs)

    def exception(self, message, **kwargs):
        kwargs["exception"] = True
        self.log("ERROR", message, **kwargs)

    def _filepath(self):
        return os.path.join("logs", self.filename)
    



def list_to_tree(data):
    """
    将扁平 TOC 列表转换为树结构（稳健版，两遍构树）。

    设计目标：
    1) 保留 structure，不再在后处理中丢失章节号；
    2) 增加 full_title，便于检索和展示；
    3) 两遍构树：先建节点，再挂父子，避免“父节点在后面”导致挂载失败；
    4) key 不直接用 raw structure（尤其 None），为无编号条目分配稳定 node_key。
    """

    def _normalize_structure(value):
        # 章节号标准化：去首尾空格、去末尾点、合并连续点，避免 "4..1." 之类脏值影响建树。
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        text = text.replace(" ", "")
        text = text.replace("．", ".")
        text = text.strip("()[]{}")
        text = re.sub(r"[^0-9.]", "", text)
        text = re.sub(r"\.+", ".", text).strip(".")
        return text or None

    def _parent_structure(structure):
        if not structure:
            return None
        if "." not in structure:
            return None
        parent = structure.rsplit(".", 1)[0].strip(".")
        return parent or None

    def _ancestor_structures(structure):
        """
        返回从近到远的祖先结构:
        4.2.1 -> [4.2, 4]
        """
        ancestors = []
        current = _parent_structure(structure)
        while current:
            ancestors.append(current)
            current = _parent_structure(current)
        return ancestors

    # Pass 1：先把所有节点建好并放入映射，确保后续挂载时父节点一定“可查”。
    nodes_by_key = {}
    # structure -> [node_key, ...]，保留全部出现位置，便于重复章节号时选择更合适父节点。
    keys_by_structure = {}
    order_index = {}
    order = []

    for idx, item in enumerate(data):
        structure = _normalize_structure(item.get("structure"))
        title = str(item.get("title") or "").strip()
        # 无章节号时 full_title 回退为 title；有章节号时自动拼接。
        full_title = f"{structure} {title}".strip() if structure else title

        # 节点唯一 key：优先复用已有 node_id；否则为无编号项生成稳定 key。
        node_key = str(item.get("node_id") or f"toc_{idx:05d}")

        node = {
            # 兼容下游：大多数逻辑只读 title，这里直接写成“章节号 + 标题”。
            "title": full_title,
            "display_title": full_title,
            "raw_title": title,
            "full_title": full_title,
            "structure": structure,
            "start_index": item.get("start_index"),
            "end_index": item.get("end_index"),
            "nodes": [],
            "_node_key": node_key,  # 内部字段，清理阶段会删除
        }
        # Front Matter 页内锚点（向后兼容：老数据不存在这些字段）。
        if item.get("start_line") is not None:
            node["start_line"] = item.get("start_line")
        if item.get("end_line") is not None:
            node["end_line"] = item.get("end_line")
        if item.get("retrieval_disabled") is not None:
            node["retrieval_disabled"] = bool(item.get("retrieval_disabled"))
        if item.get("toc_span") is not None:
            node["toc_span"] = bool(item.get("toc_span"))

        nodes_by_key[node_key] = node
        order.append(node_key)
        order_index[node_key] = idx
        if structure:
            keys_by_structure.setdefault(structure, []).append(node_key)

    def _pick_parent_key(ancestor_structure, current_node_key):
        candidates = keys_by_structure.get(ancestor_structure) or []
        if not candidates:
            return None

        current_idx = order_index.get(current_node_key, 10**9)
        # 优先选“当前节点之前最近出现”的同结构节点，避免重复章节号时挂到很早的旧节点上。
        prior = [key for key in candidates if order_index.get(key, -1) < current_idx]
        if prior:
            return prior[-1]

        # 若父节点出现在后面（TOC 顺序异常），回退到首次出现，兼容原两遍构树设计。
        return candidates[0]

    # Pass 2：再统一挂父子关系。
    root_keys = []
    for node_key in order:
        node = nodes_by_key[node_key]
        structure = node.get("structure")

        # 无编号项（Preface/Appendix/Index 等）直接归根，避免 None key 覆盖问题。
        if not structure:
            root_keys.append(node_key)
            continue

        parent_key = None
        # 先找直接父，再逐级向上找最近存在的祖先（父层缺失时也尽量保持树完整）。
        for ancestor in _ancestor_structures(structure):
            parent_key = _pick_parent_key(ancestor, node_key)
            if parent_key:
                break

        if not parent_key:
            # 父章节缺失时安全回退到根（保持结果可用）。
            root_keys.append(node_key)
            continue

        if parent_key == node_key:
            root_keys.append(node_key)
            continue

        nodes_by_key[parent_key]["nodes"].append(node)

    def _clean(node):
        # 清理内部字段，并递归去掉空 children。
        node.pop("_node_key", None)
        if not node["nodes"]:
            node.pop("nodes", None)
            return node
        node["nodes"] = [_clean(child) for child in node["nodes"]]
        return node

    return [_clean(nodes_by_key[key]) for key in root_keys]

def add_preface_if_needed(data):
    if not isinstance(data, list) or not data:
        return data

    if data[0]['physical_index'] is not None and data[0]['physical_index'] > 1:
        preface_node = {
            "structure": "0",
            "title": "Preface",
            "physical_index": 1,
        }
        data.insert(0, preface_node)
    return data



def get_page_tokens(pdf_path, model="gpt-4o-2024-11-20", pdf_parser="PyPDF2"):
    enc = tiktoken.encoding_for_model(model)
    if pdf_parser == "PyPDF2":
        pdf_reader = PyPDF2.PdfReader(pdf_path)
        page_list = []
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            page_text = page.extract_text()
            token_length = len(enc.encode(page_text))
            page_list.append((page_text, token_length))
        return page_list
    elif pdf_parser == "PyMuPDF":
        if isinstance(pdf_path, BytesIO):
            pdf_stream = pdf_path
            doc = pymupdf.open(stream=pdf_stream, filetype="pdf")
        elif isinstance(pdf_path, str) and os.path.isfile(pdf_path) and pdf_path.lower().endswith(".pdf"):
            doc = pymupdf.open(pdf_path)
        page_list = []
        for page in doc:
            page_text = page.get_text()
            token_length = len(enc.encode(page_text))
            page_list.append((page_text, token_length))
        return page_list
    else:
        raise ValueError(f"Unsupported PDF parser: {pdf_parser}")

        

def get_text_of_pdf_pages(pdf_pages, start_page, end_page):
    text = ""
    for page_num in range(start_page-1, end_page):
        text += pdf_pages[page_num][0]
    return text

def get_text_of_pdf_pages_with_labels(pdf_pages, start_page, end_page):
    text = ""
    for page_num in range(start_page-1, end_page):
        text += f"<physical_index_{page_num+1}>\n{pdf_pages[page_num][0]}\n<physical_index_{page_num+1}>\n"
    return text

def get_number_of_pages(pdf_path):
    pdf_reader = PyPDF2.PdfReader(pdf_path)
    num = len(pdf_reader.pages)
    return num


def get_pdf_bookmarks_toc(doc):
    """
    从 PDF 元数据书签中提取 TOC。
    返回格式:
    [
      {"structure": "1.2.3" or None, "title": "...", "physical_index": 12},
      ...
    ]
    """
    front_like_titles = {
        "abstract",
        "status of this memo",
        "copyright notice",
        "table of contents",
        "contents",
        "toc",
        "foreword",
        "preface",
    }

    fitz_doc = None
    close_doc = False
    try:
        if isinstance(doc, BytesIO):
            fitz_doc = pymupdf.open(stream=doc.getvalue(), filetype="pdf")
            close_doc = True
        elif isinstance(doc, str) and os.path.isfile(doc) and doc.lower().endswith(".pdf"):
            fitz_doc = pymupdf.open(doc)
            close_doc = True
        else:
            return []

        raw_toc = fitz_doc.get_toc() or []
        if not raw_toc:
            return []

        max_level = 0
        for entry in raw_toc:
            if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                try:
                    max_level = max(max_level, int(entry[0]))
                except Exception:
                    continue
        if max_level <= 0:
            return []

        counters = [0] * (max_level + 2)
        last_level = 0
        results = []
        first_explicit_page = None

        for entry in raw_toc:
            if not isinstance(entry, (list, tuple)) or len(entry) < 3:
                continue

            try:
                level = int(entry[0])
                page = int(entry[2])
            except Exception:
                continue

            if level <= 0 or page <= 0:
                continue

            if level > last_level + 1:
                level = last_level + 1 if last_level > 0 else 1

            counters[level] += 1
            for idx in range(level + 1, len(counters)):
                counters[idx] = 0
            generated_structure = ".".join(str(counters[idx]) for idx in range(1, level + 1) if counters[idx] > 0)
            last_level = level

            title_raw = " ".join(str(entry[1] or "").strip().split())
            if not title_raw:
                continue

            explicit_match = re.match(r"^\s*(\d+(?:\.\d+)*)[.)]?\s*(.*)$", title_raw)
            structure = None
            title = title_raw
            if explicit_match:
                structure = explicit_match.group(1).strip(".")
                remainder = explicit_match.group(2).strip()
                title = remainder or title_raw
                if first_explicit_page is None:
                    first_explicit_page = page
            else:
                normalized_title = " ".join(title_raw.lower().split()).strip(" \t\r\n-_:;,.()[]{}<>")
                if normalized_title in front_like_titles:
                    structure = None
                elif first_explicit_page is not None and page >= first_explicit_page:
                    structure = generated_structure or None

            results.append(
                {
                    "structure": structure,
                    "title": title,
                    "physical_index": page,
                }
            )

        deduped = []
        seen = set()
        for item in results:
            key = (
                item.get("structure"),
                str(item.get("title") or "").strip().lower(),
                item.get("physical_index"),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        deduped.sort(
            key=lambda item: (
                item.get("physical_index") if isinstance(item.get("physical_index"), int) else 10**9,
                str(item.get("structure") or ""),
                str(item.get("title") or ""),
            )
        )
        return deduped
    except Exception:
        return []
    finally:
        if close_doc and fitz_doc is not None:
            fitz_doc.close()



def _update_parent_indices_recursive(nodes):
    """
    自底向上更新父节点区间，保证父节点覆盖所有子节点范围。
    """

    if not nodes:
        return

    for node in nodes:
        children = node.get("nodes") or []
        if not children:
            continue

        _update_parent_indices_recursive(children)

        child_starts = [child.get("start_index") for child in children if isinstance(child.get("start_index"), int)]
        child_ends = [child.get("end_index") for child in children if isinstance(child.get("end_index"), int)]

        current_start = node.get("start_index")
        current_end = node.get("end_index")

        if child_starts:
            if isinstance(current_start, int):
                node["start_index"] = min(current_start, min(child_starts))
            else:
                node["start_index"] = min(child_starts)

        if child_ends:
            if isinstance(current_end, int):
                node["end_index"] = max(current_end, max(child_ends))
            else:
                node["end_index"] = max(child_ends)


def post_processing(structure, end_physical_index):
    # First convert page_number to start_index in flat list
    for i, item in enumerate(structure):
        start_index = item.get('physical_index')
        item['start_index'] = start_index

        # Front Matter 页内锚点节点：固定在单页，避免被正文分页逻辑扩张成并集。
        if (
            item.get("structure") is None
            and item.get("start_line") is not None
            and item.get("end_line") is not None
            and not item.get("toc_span")
        ):
            item['end_index'] = start_index if isinstance(start_index, int) else end_physical_index
            continue

        if i < len(structure) - 1:
            next_start_index = structure[i + 1].get('physical_index')
            if isinstance(next_start_index, int):
                # 回退为旧版边界策略：
                # 若下一标题明确“从页首开始”，当前节到上一页截止；否则允许同页衔接。
                if structure[i + 1].get('appear_start') == 'yes':
                    item['end_index'] = max(start_index or 1, next_start_index - 1)
                else:
                    item['end_index'] = next_start_index
            else:
                item['end_index'] = start_index if isinstance(start_index, int) else end_physical_index
        else:
            if isinstance(start_index, int):
                item['end_index'] = max(start_index, end_physical_index)
            else:
                item['end_index'] = end_physical_index

    tree = list_to_tree(structure)

    if tree:
        # 回退到旧行为：父节点页码不再强制聚合子节点范围。
        # 保留扁平 TOC 推导出的 start/end，便于观察“父节点页码接续”原始结果。
        return tree

    # fallback: 如果 tree 构建失败，返回扁平结构并清理临时字段。
    for node in structure:
        node.pop('appear_start', None)
        node.pop('physical_index', None)
    return structure

def clean_structure_post(data):
    if isinstance(data, dict):
        data.pop('page_number', None)
        data.pop('start_index', None)
        data.pop('end_index', None)
        if 'nodes' in data:
            clean_structure_post(data['nodes'])
    elif isinstance(data, list):
        for section in data:
            clean_structure_post(section)
    return data

def remove_fields(data, fields=['text']):
    if isinstance(data, dict):
        return {k: remove_fields(v, fields)
            for k, v in data.items() if k not in fields}
    elif isinstance(data, list):
        return [remove_fields(item, fields) for item in data]
    return data


def sort_nodes_by_position(nodes):
    """
    按 (start_index, start_line, node_id/title) 递归稳定排序。
    """

    if not isinstance(nodes, list):
        return nodes

    def _sort_key(node):
        start_index = node.get("start_index")
        start_line = node.get("start_line")
        node_id = node.get("node_id")
        title = node.get("title")

        start_index_key = start_index if isinstance(start_index, int) else 10**9
        start_line_key = start_line if isinstance(start_line, int) else 0
        tie_key = str(node_id) if node_id is not None else str(title or "")
        return (start_index_key, start_line_key, tie_key)

    nodes.sort(key=_sort_key)
    for node in nodes:
        if isinstance(node, dict) and isinstance(node.get("nodes"), list):
            sort_nodes_by_position(node["nodes"])
    return nodes


def print_toc(tree, indent=0):
    for node in tree:
        print('  ' * indent + node.get('display_title', node['title']))
        if node.get('nodes'):
            print_toc(node['nodes'], indent + 1)

def print_json(data, max_len=40, indent=2):
    def simplify_data(obj):
        if isinstance(obj, dict):
            return {k: simplify_data(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [simplify_data(item) for item in obj]
        elif isinstance(obj, str) and len(obj) > max_len:
            return obj[:max_len] + '...'
        else:
            return obj
    
    simplified = simplify_data(data)
    print(json.dumps(simplified, indent=indent, ensure_ascii=False))


def remove_structure_text(data):
    if isinstance(data, dict):
        data.pop('text', None)
        if 'nodes' in data:
            remove_structure_text(data['nodes'])
    elif isinstance(data, list):
        for item in data:
            remove_structure_text(item)
    return data


def check_token_limit(structure, limit=110000):
    list = structure_to_list(structure)
    for node in list:
        num_tokens = count_tokens(node['text'], model='gpt-4o')
        if num_tokens > limit:
            print(f"Node ID: {node['node_id']} has {num_tokens} tokens")
            print("Start Index:", node['start_index'])
            print("End Index:", node['end_index'])
            print("Title:", node['title'])
            print("\n")


def convert_physical_index_to_int(data):
    if isinstance(data, list):
        for i in range(len(data)):
            # Check if item is a dictionary and has 'physical_index' key
            if isinstance(data[i], dict) and 'physical_index' in data[i]:
                if isinstance(data[i]['physical_index'], str):
                    if data[i]['physical_index'].startswith('<physical_index_'):
                        data[i]['physical_index'] = int(data[i]['physical_index'].split('_')[-1].rstrip('>').strip())
                    elif data[i]['physical_index'].startswith('physical_index_'):
                        data[i]['physical_index'] = int(data[i]['physical_index'].split('_')[-1].strip())
    elif isinstance(data, str):
        if data.startswith('<physical_index_'):
            data = int(data.split('_')[-1].rstrip('>').strip())
        elif data.startswith('physical_index_'):
            data = int(data.split('_')[-1].strip())
        # Check data is int
        if isinstance(data, int):
            return data
        else:
            return None
    return data


def convert_page_to_int(data):
    for item in data:
        if 'page' in item and isinstance(item['page'], str):
            try:
                item['page'] = int(item['page'])
            except ValueError:
                # Keep original value if conversion fails
                pass
    return data


def _slice_single_page_text_by_lines(pdf_pages, page_index, start_line, end_line):
    """
    对单页文本按行切片（0-based，闭区间）。
    """

    try:
        page_text = pdf_pages[page_index - 1][0]
    except Exception:
        return ""

    lines = str(page_text or "").splitlines()
    if not lines:
        return str(page_text or "")

    try:
        s = int(start_line)
    except Exception:
        s = 0
    try:
        e = int(end_line)
    except Exception:
        e = len(lines) - 1

    if e < 0:
        return ""
    if s >= len(lines):
        return ""

    s = max(0, min(s, len(lines) - 1))
    e = min(e, len(lines) - 1)
    if e < s:
        return ""
    return "\n".join(lines[s : e + 1]).strip()


def _slice_multi_page_text_by_lines(pdf_pages, start_page, end_page, start_line, end_line):
    parts = []
    for page in range(start_page, end_page + 1):
        try:
            page_text = pdf_pages[page - 1][0]
        except Exception:
            continue

        lines = str(page_text or "").splitlines()
        if not lines:
            continue

        if page == start_page:
            try:
                p_start = int(start_line)
            except Exception:
                p_start = 0
            p_start = max(0, min(p_start, len(lines) - 1))
            p_end = len(lines) - 1
        elif page == end_page:
            p_start = 0
            try:
                p_end = int(end_line)
            except Exception:
                p_end = len(lines) - 1
            if p_end < 0:
                continue
            p_end = min(p_end, len(lines) - 1)
        else:
            p_start = 0
            p_end = len(lines) - 1

        if p_end < p_start:
            continue
        part = "\n".join(lines[p_start : p_end + 1]).strip()
        if part:
            parts.append(part)

    return "\n\n".join(parts).strip()


def _slice_multi_page_text_with_labels(pdf_pages, start_page, end_page, start_line, end_line):
    parts = []
    for page in range(start_page, end_page + 1):
        try:
            page_text = pdf_pages[page - 1][0]
        except Exception:
            continue

        lines = str(page_text or "").splitlines()
        if not lines:
            continue

        if page == start_page:
            try:
                p_start = int(start_line)
            except Exception:
                p_start = 0
            p_start = max(0, min(p_start, len(lines) - 1))
            p_end = len(lines) - 1
        elif page == end_page:
            p_start = 0
            try:
                p_end = int(end_line)
            except Exception:
                p_end = len(lines) - 1
            if p_end < 0:
                continue
            p_end = min(p_end, len(lines) - 1)
        else:
            p_start = 0
            p_end = len(lines) - 1

        if p_end < p_start:
            continue

        part = "\n".join(lines[p_start : p_end + 1]).strip()
        if part:
            parts.append(f"<physical_index_{page}>\n{part}\n<physical_index_{page}>\n")

    return "\n\n".join(parts).strip()


def _is_toc_span_node(node):
    title = str(node.get("raw_title") or node.get("title") or "").strip().lower()
    return bool(node.get("toc_span")) or title == "table of contents"


def add_node_text(node, pdf_pages):
    if isinstance(node, dict):
        start_page = node.get('start_index')
        end_page = node.get('end_index')
        start_line = node.get("start_line")
        end_line = node.get("end_line")

        if (
            isinstance(start_page, int)
            and isinstance(end_page, int)
            and start_page == end_page
            and start_line is not None
            and end_line is not None
        ):
            sliced_text = _slice_single_page_text_by_lines(pdf_pages, start_page, start_line, end_line)
            node['text'] = sliced_text
        elif (
            isinstance(start_page, int)
            and isinstance(end_page, int)
            and start_page < end_page
            and start_line is not None
            and _is_toc_span_node(node)
        ):
            sliced_text = _slice_multi_page_text_by_lines(pdf_pages, start_page, end_page, start_line, end_line)
            node['text'] = sliced_text
        else:
            node['text'] = get_text_of_pdf_pages(pdf_pages, start_page, end_page)

        if 'nodes' in node:
            add_node_text(node['nodes'], pdf_pages)
    elif isinstance(node, list):
        for index in range(len(node)):
            add_node_text(node[index], pdf_pages)
    return


def add_node_text_with_labels(node, pdf_pages):
    if isinstance(node, dict):
        start_page = node.get('start_index')
        end_page = node.get('end_index')
        start_line = node.get("start_line")
        end_line = node.get("end_line")

        if (
            isinstance(start_page, int)
            and isinstance(end_page, int)
            and start_page == end_page
            and start_line is not None
            and end_line is not None
        ):
            sliced_text = _slice_single_page_text_by_lines(pdf_pages, start_page, start_line, end_line)
            if sliced_text:
                node['text'] = f"<physical_index_{start_page}>\n{sliced_text}\n<physical_index_{start_page}>\n"
            else:
                node['text'] = ""
        elif (
            isinstance(start_page, int)
            and isinstance(end_page, int)
            and start_page < end_page
            and start_line is not None
            and _is_toc_span_node(node)
        ):
            sliced_text = _slice_multi_page_text_with_labels(pdf_pages, start_page, end_page, start_line, end_line)
            if sliced_text:
                node['text'] = sliced_text
            else:
                node['text'] = ""
        else:
            node['text'] = get_text_of_pdf_pages_with_labels(pdf_pages, start_page, end_page)

        if 'nodes' in node:
            add_node_text_with_labels(node['nodes'], pdf_pages)
    elif isinstance(node, list):
        for index in range(len(node)):
            add_node_text_with_labels(node[index], pdf_pages)
    return


async def generate_node_summary(node, model=None):
    prompt = f"""You are given a part of a document, your task is to generate a description of the partial document about what are main points covered in the partial document.

    Partial Document Text: {node['text']}
    
    Directly return the description, do not include any other text.
    """
    response = await ChatGPT_API_async(model, prompt)
    return response


async def generate_summaries_for_structure(structure, model=None):
    nodes = structure_to_list(structure)
    tasks = [generate_node_summary(node, model=model) for node in nodes]
    summaries = await asyncio.gather(*tasks)
    
    for node, summary in zip(nodes, summaries):
        node['summary'] = summary
    return structure


def create_clean_structure_for_description(structure):
    """
    Create a clean structure for document description generation,
    excluding unnecessary fields like 'text'.
    """
    if isinstance(structure, dict):
        clean_node = {}
        # Only include essential fields for description
        for key in ['title', 'node_id', 'summary', 'prefix_summary']:
            if key in structure:
                clean_node[key] = structure[key]
        
        # Recursively process child nodes
        if 'nodes' in structure and structure['nodes']:
            clean_node['nodes'] = create_clean_structure_for_description(structure['nodes'])
        
        return clean_node
    elif isinstance(structure, list):
        return [create_clean_structure_for_description(item) for item in structure]
    else:
        return structure


def generate_doc_description(structure, model=None):
    prompt = f"""Your are an expert in generating descriptions for a document.
    You are given a structure of a document. Your task is to generate a one-sentence description for the document, which makes it easy to distinguish the document from other documents.
        
    Document Structure: {structure}
    
    Directly return the description, do not include any other text.
    """
    response = ChatGPT_API(model, prompt)
    return response


def reorder_dict(data, key_order):
    if not key_order:
        return data
    return {key: data[key] for key in key_order if key in data}


def format_structure(structure, order=None):
    if not order:
        return structure
    if isinstance(structure, dict):
        if 'nodes' in structure:
            structure['nodes'] = format_structure(structure['nodes'], order)
        if not structure.get('nodes'):
            structure.pop('nodes', None)
        structure = reorder_dict(structure, order)
    elif isinstance(structure, list):
        structure = [format_structure(item, order) for item in structure]
    return structure


class ConfigLoader:
    def __init__(self, default_path: str = None):
        if default_path is None:
            default_path = Path(__file__).parent / "config.yaml"
        self._default_dict = self._load_yaml(default_path)
        default_provider = _resolve_llm_provider(None)
        default_model = ANTHROPIC_MODEL_NAME if default_provider == "anthropic" else OPENAI_MODEL_NAME
        if default_model:
            self._default_dict["model"] = default_model

    @staticmethod
    def _load_yaml(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _validate_keys(self, user_dict):
        unknown_keys = set(user_dict) - set(self._default_dict)
        if unknown_keys:
            raise ValueError(f"Unknown config keys: {unknown_keys}")

    def load(self, user_opt=None) -> config:
        """
        Load the configuration, merging user options with default values.
        """
        if user_opt is None:
            user_dict = {}
        elif isinstance(user_opt, config):
            user_dict = vars(user_opt)
        elif isinstance(user_opt, dict):
            user_dict = user_opt
        else:
            raise TypeError("user_opt must be dict, config(SimpleNamespace) or None")

        self._validate_keys(user_dict)
        merged = {**self._default_dict, **user_dict}
        return config(**merged)
