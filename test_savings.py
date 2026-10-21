"""Focused price, durable accounting and protected summary checks."""
import json
import os
import sqlite3
import tempfile
import threading
from decimal import Decimal
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from runtime_config import RuntimeConfig, input_cost


def self_check():
    assert input_cost(0) == 0 and input_cost(100000) == Decimal('.2')
    assert input_cost(272000) == Decimal('.544') and input_cost(272001) == Decimal('1.088004')
    for value in (-1, True, '1'):
        try:
            input_cost(value)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid token count')
    with tempfile.TemporaryDirectory() as directory:
        store = RuntimeConfig(Path(directory) / 'connections.db')
        assert store.savings_summary()['records'] == 0 and not store.path.exists()
        run = {'id':'a'*32,'created_at':10,'finished_at':20,'status':'completed',
               'calls':[{'target':'fixture','estimated_input_tokens':400},{'target':'fixture','estimated_input_tokens':300}],
               'metrics':{'baseline_scope':'executor_visible_prompt','single_raw_input_estimate':1000,
                          'model_calls':2,'estimated_input_tokens':700,'usage_complete':True,'reported_output_tokens':20}}
        store.save_run(run)
        store.save_run(run)
        assert store.savings_summary()['records'] == 1 and run['savings']['saved_input_usd'] == '0.0006'
        store.record_savings('failed', source='chat',created_at=30,status='failed',baseline_input=1000,inputs=[200])
        snapshot = store.savings_summary()
        assert snapshot['saved_input_tokens'] == 100 and Decimal(snapshot['saved_input_usd']) == Decimal('.0002')
        store.record_savings('unknown',source='harness',created_at=40,status='completed',baseline_input=2000,inputs=[],comparable=False)
        assert store.savings_summary()['uncounted_records'] == 1
        store.manage_history('a'*32)
        assert RuntimeConfig(store.path).savings_summary()['saved_input_usd'] == snapshot['saved_input_usd']
        store.record_savings('long',source='chat',created_at=50,status='completed',baseline_input=300000,inputs=[200000])
        assert Decimal(store.savings_summary()['saved_input_usd']) == Decimal('.8002')
        with store.connect() as db:
            assert {r[1] for r in db.execute('PRAGMA table_info(savings)')}.isdisjoint({'task','content','api_key','result'})

        # Backfill retained historical records once, using the same identifier.
        legacy = Path(directory) / 'legacy.db'
        with sqlite3.connect(legacy) as db:
            db.execute('CREATE TABLE runs(id TEXT PRIMARY KEY,data TEXT,updated REAL)')
            db.execute('INSERT INTO runs VALUES(?,?,?)',(run['id'],json.dumps(run),20))
        db.close()
        old = RuntimeConfig(legacy)
        assert old.savings_summary()['saved_input_tokens'] == 300
        assert old.savings_summary()['records'] == 1

        from model_providers import ProviderError, unified_chat
        first, second = MagicMock(), MagicMock()
        first.models.return_value = second.models.return_value = ['fixture']
        first.chat.side_effect = ProviderError('fixture failed')
        second.chat.return_value = {'choices':[{'message':{'content':'42'},'finish_reason':'stop'}]}
        with patch('model_providers._auto_candidates',return_value=['freetoken','ollama']), patch('model_providers.OpenAICompatibleProvider',side_effect=[first,second]):
            routed = unified_chat('ANSWER是多少？','ANSWER=42','general')
        assert len(routed['input_attempts']) == 2 and routed['input_attempts'][0] == routed['input_attempts'][1]

        import tokenflow as tf
        with patch.dict(os.environ, {'TOKENFLOW_CONFIG_DB':str(store.path)}):
            server = ThreadingHTTPServer(('127.0.0.1',0),tf.TokenFlowHandler)
            thread = threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                connection = HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                connection.request('GET','/api/config/savings')
                reply = connection.getresponse()
                assert reply.status == 403
                reply.read()
                connection.request('GET','/api/config/savings',headers={'X-TokenFlow-Token':tf.SESSION_TOKEN})
                reply = connection.getresponse()
                data = json.loads(reply.read())
                assert reply.status == 200 and data['records'] == 4 and data['price']['model'] == 'gpt-6.1-sol'
                store.save_profile({'id':'savings-fixture','name':'Fixture','protocol':'openai','base_url':'http://127.0.0.1:1/v1','models':['fixture']})
                payload = {'connection_id':'savings-fixture','model':'fixture','task':'target 的返回值是多少？只回答数字。',
                           'content':'def target():\n    return 42\n\n'+'\n\n'.join(f'def other_{i}():\n    return {i}' for i in range(100))}
                fixture = {'choices':[{'message':{'content':'42'},'finish_reason':'stop'}],'usage':{'prompt_tokens':7,'completion_tokens':3}}
                headers = {'X-TokenFlow-Token':tf.SESSION_TOKEN,'Content-Type':'application/json'}
                with patch('runtime_config.ConnectionClient.chat',return_value=fixture):
                    connection.request('POST','/api/chat',json.dumps(payload),headers)
                    reply = connection.getresponse()
                    result = json.loads(reply.read())
                    assert reply.status == 200 and result['savings']['saved_input_tokens'] > 0
                assert store.savings_summary()['records'] == 5
                connection.request('POST','/api/run',json.dumps(payload),headers)
                reply = connection.getresponse()
                assert reply.status == 200
                reply.read()
                assert store.savings_summary()['records'] == 5
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
    print('Savings price/persistence/summary checks: PASS')


if __name__ == '__main__':
    self_check()
