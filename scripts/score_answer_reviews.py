import argparse
import json

from finlens.review_scores import score_reviews


def main():
    parser=argparse.ArgumentParser(description='Validate supplied answer reviews; do not auto-grade semantics.')
    parser.add_argument('--results',required=True)
    parser.add_argument('--reviews',required=True)
    parser.add_argument('--cases-file',default='finlens/fixtures/answer_cases_v1.json')
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    try:r=score_reviews(args.results,args.reviews,args.cases_file,args.output_dir)
    except (ValueError,OSError,KeyError) as exc:parser.error(str(exc))
    print(json.dumps({k:v for k,v in r.items() if k!='scores'},ensure_ascii=True,indent=2))


if __name__=='__main__':main()
