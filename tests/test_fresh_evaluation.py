import unittest

from scripts.run_fresh_gemini_eval import schedule,summarize


class FreshEvaluationTests(unittest.TestCase):
    def test_schedule_has_100_requests_40_unique_cases_and_three_waves(self):
        cases=[{'id':str(i)} for i in range(40)]
        plan=schedule(cases)
        self.assertEqual(len(plan),100)
        self.assertEqual(len({c['id'] for _,c in plan}),40)
        self.assertEqual([sum(w==n for w,_ in plan) for n in [1,2,3]],[40,40,20])
        with self.assertRaises(ValueError):schedule(cases[:39])

    def test_outages_and_local_non_generation_are_not_model_success(self):
        def row(model,error,attempted,attempts):return {'response':{'generation_model':model,'generation_error':error,'generation_attempted':attempted,'generation_attempts':attempts,'latency_ms':10}}
        values=[row('m',None,True,[{'model':'m','status':'succeeded','latency_ms':4,'usage':{'totalTokenCount':15}}]),
            row(None,{'code':'timeout'},True,[{'model':'m','status':'failed','code':'timeout','latency_ms':10,'usage':None}]),row(None,None,False,[])]
        result=summarize(values)
        self.assertEqual(result['observed_chain_success_rate'],1/3)
        self.assertEqual(result['provider_response_rate_on_attempted_questions'],1/2)
        self.assertEqual(result['not_executed'],97)
        self.assertEqual(result['attempts_without_total_tokens'],1)
        self.assertEqual(result['reported_usage_totals']['totalTokenCount'],15)
