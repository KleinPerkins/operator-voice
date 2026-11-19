"""Wrapping-key writes must not expose key material through process arguments."""
import base64
import shlex
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from operator_voice.persistence.crypto import KeyStoreError, MacKeychainKeyStore


class KeychainTransportTests(unittest.TestCase):
    def test_key_never_appears_in_process_arguments(self):
        key = b'public-regression-fixture-only'
        encoded = base64.b64encode(key).decode('ascii')
        with patch('operator_voice.persistence.crypto.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            MacKeychainKeyStore().set('fixture-account', key)
        args, kwargs = run.call_args
        self.assertNotIn(encoded, repr(args))
        self.assertNotIn(key.decode(), repr(args))
        self.assertEqual(args[0], ['/usr/bin/security', '-q', '-i'])
        self.assertEqual(shlex.split(kwargs['input']), [
            'add-generic-password', '-a', 'fixture-account', '-s', 'operator-voice',
            '-w', encoded, '-U',
        ])

    def test_account_service_and_scoped_path_are_quoted_as_data(self):
        store = MacKeychainKeyStore(service='fixture service "quoted"', keychain_path="/tmp/fixture 'keychain'.keychain-db")
        with patch('operator_voice.persistence.crypto.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            store.set("fixture account 'quoted'", b'fixture')
        tokens = shlex.split(run.call_args.kwargs['input'])
        self.assertEqual(tokens[tokens.index('-a') + 1], "fixture account 'quoted'")
        self.assertEqual(tokens[tokens.index('-s') + 1], 'fixture service "quoted"')
        self.assertEqual(tokens[-1], "/tmp/fixture 'keychain'.keychain-db")

    def test_line_delimiters_are_rejected_before_process_launch(self):
        for delimiter in ('\n', '\r', '\0'):
            for field in ('account', 'service', 'keychain_path'):
                with self.subTest(field=field, delimiter=repr(delimiter)):
                    store = MacKeychainKeyStore()
                    account = 'fixture-account'
                    if field == 'account': account += delimiter
                    else: setattr(store, field, 'fixture' + delimiter)
                    with patch('operator_voice.persistence.crypto.subprocess.run') as run:
                        with self.assertRaises(KeyStoreError): store.set(account, b'fixture')
                    run.assert_not_called()

    def test_oversized_command_is_rejected_before_process_launch(self):
        with patch('operator_voice.persistence.crypto.subprocess.run') as run:
            with self.assertRaises(KeyStoreError):
                MacKeychainKeyStore().set('x' * 4096, b'fixture')
        run.assert_not_called()

    def test_failure_diagnostics_cannot_echo_a_key(self):
        key = b'public-failure-fixture'
        encoded = base64.b64encode(key).decode('ascii')
        failure = SimpleNamespace(returncode=1, stderr='parser echoed ' + encoded)
        with patch('operator_voice.persistence.crypto.subprocess.run', return_value=failure):
            with self.assertRaises(KeyStoreError) as error:
                MacKeychainKeyStore().set('fixture-account', key)
        self.assertEqual(str(error.exception), 'Keychain write failed')
        self.assertNotIn(encoded, str(error.exception))


if __name__ == '__main__': unittest.main()
