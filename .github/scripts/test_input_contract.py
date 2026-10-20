"""Run with python -m unittest discover -s .github/scripts -p test_input_contract.py."""
import ast
import base64
from pathlib import Path
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / '.github/actions/decrypt-secrets/action.yml').read_text()
CODE = textwrap.dedent(SOURCE.split('      run: |\n', 1)[1]).split('\nimport os\n', 1)[0]
NAMESPACE = {}
exec(compile(CODE, 'decrypt-secrets-validator', 'exec'), NAMESPACE)
validate = NAMESPACE['validate_inputs']


def fixture():
    data = dict.fromkeys(NAMESPACE['FIELDS'], 'test')
    data.update(serverPort='21116', custom=base64.b64encode(b'{}').decode(), appname='RustDesk Admin')
    for name in ('delayFix', 'rdgen', 'xOffline', 'removeNewVersionNotif'):
        data[name] = 'false'
    return data


class InputContractTests(unittest.TestCase):
    def test_current_producer_schema(self):
        tree = ast.parse((ROOT / 'rdgenerator/views.py').read_text())
        schemas = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = {key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
                if {'custom', 'serverPort', 'uuid'} <= keys:
                    schemas.append(keys)
        self.assertEqual(len(schemas), 1)
        data = fixture()
        data.update(slug_appname='rustdesk-admin', update_public_key='')
        self.assertEqual(set(data), schemas[0])
        self.assertEqual(set(validate(data)), schemas[0])

    def test_legacy_and_optional_combinations(self):
        for slug in (False, True):
            for key in (False, True):
                with self.subTest(slug=slug, key=key):
                    data = fixture()
                    if slug:
                        data['slug_appname'] = 'rustdesk-admin'
                    if key:
                        data['update_public_key'] = ''
                    before = dict(data)
                    self.assertEqual(validate(data)['slug_appname'], 'rustdesk-admin')
                    self.assertEqual(data, before)

    def test_public_key_encodings(self):
        for key in ('', bytes(range(32)).hex(), base64.b64encode(bytes(range(32))).decode()):
            self.assertEqual(validate(dict(fixture(), update_public_key=key))['update_public_key'], key)

    def test_invalid_input_rejected(self):
        for key in ('not-a-key', 'aa', base64.b64encode(b'x' * 31).decode(), base64.b64encode(b'x' * 33).decode(), '$(id)', 'x;echo', 'x\nY', None):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(dict(fixture(), update_public_key=key))
        for key in ('', '../path', '$(id)', None):
            with self.subTest(slug=key), self.assertRaises(ValueError):
                validate(dict(fixture(), slug_appname=key))
        with self.assertRaises(ValueError):
            validate(dict(fixture(), unknown='value'))
        data = fixture()
        del data['custom']
        with self.assertRaises(ValueError):
            validate(data)


if __name__ == '__main__':
    unittest.main()
