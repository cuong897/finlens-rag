"""Run bounded answer checks on a temporary synthetic corpus, never user reports."""
import argparse
import json
from pathlib import Path
import tempfile

from finlens.answer_check import run_answer_checks
from finlens.engine import Engine
from finlens.environment import load_env_file
from finlens.ingest import seed_demo
from finlens.local import prepare_embeddings
from finlens.providers import ModelConfig, gemini_key_configured
from finlens.store import Store


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--env-file')
    parser.add_argument('--max-model-questions',type=int,default=2)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--case-id',action='append',help='Run only named cases, without repeating the earlier pilot.')
    args=parser.parse_args()
    if not 0<=args.max_model_questions<=10:parser.error('Giới hạn từ 0 đến 10 câu hỏi AI mỗi lượt.')
    output=Path(args.output_dir).resolve();output.mkdir(parents=True,exist_ok=False)
    if args.env_file:load_env_file(args.env_file)
    config=ModelConfig(chat_provider='extractive')
    if args.live:
        if not gemini_key_configured():parser.error('Cần API key trong tệp cấu hình để chạy live.')
        config=ModelConfig.from_env()
        if config.chat_provider!='gemini' or not config.chat_model:parser.error('Live cần cấu hình Gemini.')
        config=prepare_embeddings('data',config)
    dataset=json.loads((Path(__file__).resolve().parents[1]/'finlens/fixtures/answer_cases_v1.json').read_text(encoding='utf-8'))
    if args.case_id:
        wanted=set(args.case_id)
        if wanted-{c['id'] for c in dataset['cases']}:parser.error('Case ID không tồn tại.')
        dataset={**dataset,'cases':[c for c in dataset['cases'] if c['id'] in wanted]}
    with tempfile.TemporaryDirectory(prefix='answer-check-',dir=output) as work:
        store=Store(work);seed_demo(store);engine=Engine(store,config)
        if args.live:engine.index_embeddings()
        report=run_answer_checks(engine,dataset,output,live=args.live,max_model_questions=args.max_model_questions)
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=True,indent=2))


if __name__=='__main__':main()
