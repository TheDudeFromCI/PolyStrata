"""Run with POLY_GRAMMAR_TEST=/path/to/poly-json-grammar-test; never uses models."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

import jsonschema
from serve import poly_grammar
from serve.structured import prepare_format


class Vocabulary:
    tokens = [str(i) for i in range(260)]
    token_types = [1] * 256 + [3, 4, 4, 1]
    special_tokens = {'</think>': 257, '<think>': 258}

    def token_bytes(self, t):
        return bytes([t]) if t < 256 else b'{"x":' if t == 259 else b''


@unittest.skipUnless(os.environ.get('POLY_GRAMMAR_TEST'), 'build poly-json-grammar-test and set POLY_GRAMMAR_TEST')
class Native(unittest.TestCase):
    def run_fixture(self, schema, text, reasoning=None, ids=None):
        with tempfile.NamedTemporaryFile() as f:
            poly_grammar.write_request(f, Vocabulary(), {256}, schema)
            f.flush()
            seq = ([] if reasoning is None else list(reasoning.encode()) + [257])
            seq += list(text.encode()) if ids is None else ids
            return subprocess.run([os.environ['POLY_GRAMMAR_TEST'], f.name,
                                   'final' if reasoning is None else 'reasoning'],
                                  input=' '.join(map(str, seq)), text=True, capture_output=True)

    def valid(self, schema, value, reasoning=None):
        jsonschema.Draft202012Validator(schema).validate(value)
        result = self.run_fixture(schema, json.dumps(value, ensure_ascii=False, separators=(',', ':')), reasoning)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pydantic_shapes(self):
        child = {'type': 'object', 'properties': {'s': {'type': 'string', 'minLength': 1},
                  'n': {'type': 'integer', 'minimum': 2, 'maximum': 7}, 'b': {'type': 'boolean'}},
                 'required': ['s', 'n', 'b'], 'additionalProperties': False}
        schema = {'type': 'object', 'properties': {'children': {'type': 'array', 'items': child},
                  'optional': {'anyOf': [{'type': 'null'}, {'enum': ['yes', 'no']}]},
                  'literal': {'const': 'ok'}}, 'required': ['children', 'optional', 'literal'],
                  'additionalProperties': False}
        self.valid(schema, {'children': [{'s': 'hé😀"\\\n', 'n': 3, 'b': True}], 'optional': None, 'literal': 'ok'})

    def test_sampled_metadata_reproduces_original_error(self):
        with tempfile.NamedTemporaryFile() as f:
            poly_grammar.write_request(f,Vocabulary(),{256},{'type':'object'})
            f.flush()
            result=subprocess.run([os.environ['POLY_GRAMMAR_TEST'],f.name,'metadata_sample'],
                input='123 125',text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('request_vocab=260',result.stderr)
            self.assertIn('model_vocab=260',result.stderr)
            self.assertIn('count=260,retained=64',result.stderr)

    def test_refs_and_numeric(self):
        schema = {'type': 'object', '$defs': {'n': {'type': 'number', 'minimum': 0.5,
                  'exclusiveMaximum': 3, 'multipleOf': 0.5}}, 'properties': {'x': {'$ref': '#/$defs/n'}},
                  'required': ['x'], 'additionalProperties': False}
        self.valid(schema, {'x': 1.5})
        for text in ['{"x":-1}', '{"x":3}', '{"x":1.3}', '{}', '{"x":1,"y":2}']:
            with self.subTest(text=text):
                self.assertNotEqual(self.run_fixture(schema, text).returncode, 0)

    def test_reasoning_is_unrestricted_and_isolated(self):
        schema = {'type': 'object', 'properties': {'x': {'const': True}}, 'required': ['x']}
        for reasoning in ['', 'code <think> literal </think> { invalid JSON \\ prose math λ', 'abc ' * 10000]:
            self.valid(schema, {'x': True}, reasoning)
        self.valid(schema, {'x': True})  # new matcher on a consecutive request

    def test_premature_eos_and_budget(self):
        schema = {'type': 'object'}
        for reasoning in (None, ''):
            self.assertNotEqual(self.run_fixture(schema, '', reasoning, [256]).returncode, 0)
        self.assertNotEqual(self.run_fixture(schema, '{').returncode, 0)
        self.assertNotEqual(self.run_fixture(schema, '', ids=[ord('x')]).returncode, 0)

    def test_multibyte_token(self):
        schema = {'type': 'object', 'properties': {'x': {'const': 1}}, 'required': ['x']}
        self.assertEqual(self.run_fixture(schema, '', ids=[259, ord('1'), ord('}')]).returncode, 0)

    def test_unsupported_constraints(self):
        # Actual upstream strict compiler, not just the HTTP keyword guard.
        self.assertNotEqual(self.run_fixture({'type': 'object', 'not': {'type': 'object'}}, '{}').returncode, 0)

    def test_no_finite_valid_token_and_eos_during_reasoning(self):
        with tempfile.NamedTemporaryFile() as f:
            poly_grammar.write_request(f, Vocabulary(), {256}, {'type':'object'})
            f.flush()
            for mode, token in [('nonfinite',123),('reasoning',256)]:
                result=subprocess.run([os.environ['POLY_GRAMMAR_TEST'],f.name,mode],
                    input=str(token),text=True,capture_output=True)
                self.assertNotEqual(result.returncode,0)



class Protocol(unittest.TestCase):
    @unittest.skipUnless(Path(os.environ.get('POLY_GRAMMAR_CHECK', '/tmp/poly-grammar-check')).exists(), 'native preflight checker required')
    def test_request_file_is_removed_at_context_exit(self):
        checker=Path(os.environ.get('POLY_GRAMMAR_CHECK','/tmp/poly-grammar-check'))
        engine=SimpleNamespace(json_schema_llguidance=True, spawn=(str(checker.with_name('strata-poly')),[]))
        with poly_grammar.request(engine,Vocabulary(),{256},{'type':'object'}) as (path, close):
            self.assertTrue(Path(path).exists())
            self.assertEqual(close,257)
        self.assertFalse(Path(path).exists())

    def test_no_fallback(self):
        with self.assertRaises(ValueError):
            with poly_grammar.request(SimpleNamespace(), Vocabulary(), {256}, {'type': 'object'}):
                self.fail('unsupported engine accepted')

    def test_capability_rejection_reasons(self):
        engine=SimpleNamespace(json_schema_llguidance=False, info={})
        self.assertIn("did not advertise",poly_grammar.unavailable_reason(engine))
        for reason,message in poly_grammar.REASONS.items():
            engine.info={"json_schema_reason":reason}
            self.assertEqual(poly_grammar.unavailable_reason(engine),message)
        engine.json_schema_llguidance=True
        self.assertIsNone(poly_grammar.unavailable_reason(engine))
        engine.batch=1
        self.assertIn("Batching",poly_grammar.unavailable_reason(engine))

    def test_extensions_cannot_weaken_grammar(self):
        for key in ('x-guidance', 'not', 'uniqueItems', 'unevaluatedProperties'):
            with self.assertRaises(ValueError):
                poly_grammar.check_keywords({'type': 'object', key: True})

    def test_prepare_preserves_client_messages(self):
        messages = [{'role': 'user', 'content': 'Solve it'}]
        fmt = {'type': 'json_schema', 'json_schema': {'name': 'answer', 'strict': True,
               'schema': {'type': 'object', 'properties': {'x': {'type': 'integer'}}, 'required': ['x']}}}
        prepared, validator = prepare_format(fmt, messages, constrained=True)
        self.assertEqual(messages, [{'role': 'user', 'content': 'Solve it'}])
        self.assertIsNotNone(validator)


class API(unittest.TestCase):
    """HTTP integration with scripted inference and real native grammar verification."""
    def setUp(self):
        from serve.server import ByteTokenizer, MockEngine, Service, serve
        from serve.frontend import ChatTemplate
        class Tok(ByteTokenizer):
            SPECIALS = ByteTokenizer.SPECIALS + ['<think>', '</think>']
            ALWAYS = ('<think>', '</think>')
            tokens = [str(i) for i in range(256)] + SPECIALS
            token_types = [1] * 256 + [3] * len(SPECIALS)
            special_tokens = {s: i + 256 for i, s in enumerate(SPECIALS)}
            def token_bytes(self, t):
                return bytes([t]) if t < 256 else self.SPECIALS[t-256].encode()
        self.tok = Tok()
        class Engine(MockEngine):
            json_schema_llguidance = True
            spawn = (str(Path(os.environ.get('POLY_GRAMMAR_CHECK', '/tmp/poly-grammar-check')).with_name('strata-poly')), [])
        self.engine = Engine(self.tok, 'Thought {invalid}</think>{"x":1}', max_context=8192)
        self.svc = Service(self.engine, self.tok, ChatTemplate(Path(__file__).parent/'chat_template.jinja'))
        self.httpd = serve(self.svc, port=0)
        self.calls = []
        original = self.engine.generate
        def generate(ids, max_new, sampling, cancel, **kw):
            self.calls.append(dict(sampling))
            yield from original(ids, max_new, sampling, cancel, **kw)
        self.engine.generate = generate

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def chat(self, **overrides):
        import urllib.request, urllib.error
        request = {'messages': [{'role':'user','content':'Solve'}], 'reasoning_effort':'high',
                   'response_format': {'type':'json_schema','json_schema':{'name':'answer','strict':True,
                   'schema': {'type':'object','properties':{'x':{'const':1}},'required':['x'],
                              'additionalProperties':False}}}, **overrides}
        req = urllib.request.Request(f'http://127.0.0.1:{self.httpd.server_address[1]}/v1/chat/completions',
                                     json.dumps(request).encode(), {'Content-Type':'application/json'})
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as e:
            response = e
        with response:
            return response.status, response.read().decode()

    @unittest.skipUnless(Path(os.environ.get('POLY_GRAMMAR_CHECK', '/tmp/poly-grammar-check')).exists(), 'native preflight checker required')
    def test_native_request_transport_and_reasoning(self):
        for effort in ('low','medium','high'):
            status, raw = self.chat(reasoning_effort=effort)
            self.assertEqual(status,200,raw)
            message = json.loads(raw)['choices'][0]['message']
            self.assertEqual(json.loads(message['content']), {'x':1})
            self.assertIn('Thought {invalid}', message['reasoning_content'])
            self.assertTrue(self.calls[-1]['_poly_reasoning'])
            self.assertIn('_poly_grammar',self.calls[-1])

    @unittest.skipUnless(Path(os.environ.get('POLY_GRAMMAR_CHECK', '/tmp/poly-grammar-check')).exists(), 'native preflight checker required')
    def test_completion_without_eos_and_no_reasoning(self):
        def generate(ids, max_new, sampling, cancel, **kw):
            self.calls.append(dict(sampling))
            yield from b'{"x":1}'
            self.engine.last = {'finish': 'stop'}
        self.engine.generate = generate
        for fmt in ({'type':'json_object'}, {'type':'json_schema','json_schema':{'name':'answer',
                    'schema':{'type':'object','properties':{'x':{'type':'integer'}},'required':['x']}}}):
            status, raw = self.chat(reasoning_effort='none', response_format=fmt)
            self.assertEqual(status,200,raw)
            self.assertFalse(self.calls[-1]['_poly_reasoning'])
            self.assertEqual(json.loads(raw)['choices'][0]['finish_reason'],'stop')

    @unittest.skipUnless(Path(os.environ.get('POLY_GRAMMAR_CHECK', '/tmp/poly-grammar-check')).exists(), 'native preflight checker required')
    def test_buffered_stream_and_incomplete_failure(self):
        status, raw = self.chat(stream=True)
        self.assertEqual(status,200,raw)
        chunks=[json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: {')]
        content=''.join(c.get('choices',[{}])[0].get('delta',{}).get('content','') for c in chunks)
        self.assertEqual(json.loads(content),{'x':1})
        self.assertIn('reasoning_content',raw)
        code, raw = self.chat(max_tokens=2)
        self.assertEqual(code,502,raw)
        self.assertIn('structured_output_failed',raw)

    @unittest.skipUnless(Path(os.environ.get('POLY_GRAMMAR_CHECK', '/tmp/poly-grammar-check')).exists(), 'native preflight checker required')
    def test_partial_reasoning_utf8_does_not_enter_json(self):
        self.engine.script = [0xe2, self.tok.special_tokens['</think>']] + list(b'{"x":1}') + [257]
        self.engine.scripts = [self.engine.script]
        code,raw=self.chat()
        self.assertEqual(code,200,raw)
        msg=json.loads(raw)['choices'][0]['message']
        self.assertEqual(msg['reasoning_content'],'\ufffd')
        self.assertEqual(json.loads(msg['content']),{'x':1})

    def test_client_cannot_inject_private_protocol_keys(self):
        status, raw = self.chat(response_format={'type':'text'}, _poly_grammar='/etc/passwd')
        self.assertEqual(status,200,raw)
        self.assertNotIn('_poly_grammar',self.calls[-1])

    def run_native_http_requests(self, requests):
        from serve.structured import StructuredOutputError
        answers=[]
        def generate(ids,max_new,sampling,cancel,**kw):
            self.calls.append(dict(sampling))
            text=answers.pop(0)
            result=subprocess.run([os.environ['POLY_GRAMMAR_TEST'],sampling['_poly_grammar'],'final'],
                input=' '.join(map(str,text.encode())),text=True,capture_output=True)
            if result.returncode:
                raise StructuredOutputError(result.stderr)
            yield from text.encode()
            self.engine.last={'finish':'stop'}
        self.engine.generate=generate
        for schema,value in requests:
            answers.append(json.dumps(value,separators=(',',':')))
            code,raw=self.chat(reasoning_effort='none',stream=True,response_format={
                'type':'json_schema','json_schema':{'name':'answer','strict':True,'schema':schema}})
            self.assertEqual(code,200,raw)
            chunks=[json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: {')]
            self.assertFalse(any('error' in chunk for chunk in chunks),raw)
            content=''.join(chunk['choices'][0]['delta'].get('content','') for chunk in chunks)
            parsed=json.loads(content)
            jsonschema.Draft202012Validator(schema).validate(parsed)
            self.assertEqual(parsed,value)

    @unittest.skipUnless(os.environ.get('POLY_GRAMMAR_TEST') and Path(os.environ.get('POLY_GRAMMAR_CHECK','/tmp/poly-grammar-check')).exists(),'native fixtures required')
    def test_direct_api_simple_schema_cold_service(self):
        self.run_native_http_requests([({'type':'object','properties':{'x':{'const':1}},
            'required':['x'],'additionalProperties':False},{'x':1})])

    @unittest.skipUnless(os.environ.get('POLY_GRAMMAR_TEST') and Path(os.environ.get('POLY_GRAMMAR_CHECK','/tmp/poly-grammar-check')).exists(),'native fixtures required')
    def test_direct_api_repeated_alternating_schemas_warm_service(self):
        a=({'type':'object','properties':{'x':{'const':1}},'required':['x'],'additionalProperties':False},{'x':1})
        b=({'type':'object','properties':{'label':{'enum':['warm','cold']}},'required':['label'],'additionalProperties':False},{'label':'warm'})
        self.run_native_http_requests([a,a,b,a,b,b,a])

    def test_capability_reporting(self):
        self.assertTrue(self.svc.v1_status()['structured_output']['constrained_decoding'])
        self.assertEqual(self.svc.v1_status()['structured_output']['method'],'llguidance')
        self.engine.json_schema_llguidance=False
        self.assertFalse(self.svc.v1_status()['structured_output']['constrained_decoding'])

    def test_invalid_schema_precedes_generation(self):
        code, raw = self.chat(response_format={'type':'json_schema','json_schema':{'name':'bad','schema':
            {'type':'object','properties':{'x':{'not':{'type':'integer'}}}}}})
        self.assertEqual(code,400,raw)
        self.assertEqual(self.calls,[])

    def test_ordinary_chat_has_no_grammar(self):
        status, raw = self.chat(response_format={'type':'text'})
        self.assertEqual(status,200,raw)
        self.assertNotIn('_poly_grammar',self.calls[-1])

    @unittest.skipUnless(Path(os.environ.get('POLY_GRAMMAR_CHECK', '/tmp/poly-grammar-check')).exists(), 'native preflight checker required')
    def test_literal_markers_are_not_transitions(self):
        literal = 'ordinary text </think> {bad json} <think> more'
        self.engine.script = list(literal.encode()) + [self.tok.special_tokens['</think>']] + list(b'{"x":1}') + [257]
        self.engine.scripts = [self.engine.script]
        code, raw = self.chat()
        self.assertEqual(code,200,raw)
        msg=json.loads(raw)['choices'][0]['message']
        self.assertEqual(msg['reasoning_content'],literal)
        self.assertEqual(json.loads(msg['content']),{'x':1})

    @unittest.skipUnless(Path(os.environ.get('POLY_GRAMMAR_CHECK', '/tmp/poly-grammar-check')).exists(), 'native preflight checker required')
    def test_budget_wrap_resumes_with_fresh_final_grammar(self):
        self.engine.scripts = [list(b'long reasoning that will be closed by its budget'), list(b'{"x":1}') + [257]]
        code, raw = self.chat(reasoning_budget_tokens=2,max_tokens=512)
        self.assertEqual(code,200,raw)
        self.assertEqual(len(self.calls),2)
        self.assertTrue(self.calls[0]['_poly_reasoning'])
        self.assertFalse(self.calls[1]['_poly_reasoning'])
        self.assertEqual(json.loads(json.loads(raw)['choices'][0]['message']['content']),{'x':1})

    def test_unsupported_engine_fails_before_generation(self):
        self.engine.json_schema_llguidance=False
        code,raw=self.chat()
        self.assertEqual(code,400,raw)
        self.assertEqual(self.calls,[])

if __name__ == '__main__':
    unittest.main()
