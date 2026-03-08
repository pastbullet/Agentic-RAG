import argparse
import os
import json
from pageIndex import *

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process PDF document and generate structure')
    parser.add_argument('--pdf_path', type=str, required=True, help='Path to the PDF file')

    parser.add_argument('--model', type=str, default='gpt-4o-2024-11-20', help='Model to use')
    parser.add_argument(
        '--llm-provider',
        type=str,
        choices=['openai', 'anthropic'],
        default=None,
        help='LLM provider to use for API calls',
    )

    parser.add_argument('--toc-check-pages', type=int, default=20, 
                      help='Number of pages to check for table of contents (PDF only)')
    parser.add_argument('--max-pages-per-node', type=int, default=10,
                      help='Maximum number of pages per node (PDF only)')
    parser.add_argument('--max-tokens-per-node', type=int, default=20000,
                      help='Maximum number of tokens per node (PDF only)')

    parser.add_argument('--if-add-node-id', type=str, default='yes',
                      help='Whether to add node id to the node')
    parser.add_argument('--if-add-node-summary', type=str, default='yes',
                      help='Whether to add summary to the node')
    parser.add_argument('--if-add-doc-description', type=str, default='no',
                      help='Whether to add doc description to the doc')
    parser.add_argument('--if-add-node-text', type=str, default='no',
                      help='Whether to add text to the node')
                      
    args = parser.parse_args()

    if args.llm_provider:
        os.environ["PROTOCOL_TWIN_LLM_PROVIDER"] = args.llm_provider

    if not args.pdf_path.lower().endswith('.pdf'):
        raise ValueError("PDF file must have .pdf extension")
    if not os.path.isfile(args.pdf_path):
        raise ValueError(f"PDF file not found: {args.pdf_path}")

    opt = config(
        model=args.model,
        toc_check_page_num=args.toc_check_pages,
        max_page_num_each_node=args.max_pages_per_node,
        max_token_num_each_node=args.max_tokens_per_node,
        if_add_node_id=args.if_add_node_id,
        if_add_node_summary=args.if_add_node_summary,
        if_add_doc_description=args.if_add_doc_description,
        if_add_node_text=args.if_add_node_text
    )

    toc_with_page_number = page_index_main(args.pdf_path, opt)
    print('Parsing done, saving to file...')

    pdf_name = os.path.splitext(os.path.basename(args.pdf_path))[0]
    output_dir = './results'
    output_file = f'{output_dir}/{pdf_name}_structure.json'
    os.makedirs(output_dir, exist_ok=True)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(toc_with_page_number, f, indent=2)

    print(f'Tree structure saved to: {output_file}')
