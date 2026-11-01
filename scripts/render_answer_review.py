import argparse
import json
from finlens.review_page import render_review_page


def main():
    parser=argparse.ArgumentParser(description='Create a local self-contained review worksheet.')
    parser.add_argument('--results',required=True)
    parser.add_argument('--cases-file',default='finlens/fixtures/answer_cases_v1.json')
    parser.add_argument('--output-file',required=True)
    args=parser.parse_args()
    print(json.dumps(render_review_page(args.results,args.cases_file,args.output_file)))


if __name__=='__main__':main()
