from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from youtube_commands import CommandRegistry, IncomingMessage


class CommandTests(unittest.TestCase):
    def test_empty_registry_ignores_all_commands(self):
        self.assertIsNone(CommandRegistry().dispatch(IncomingMessage('id','chat','viewer','!ile'), 'bot'))

    def test_handler_extension_and_stable_delivery_key(self):
        registry = CommandRegistry()
        registry.register('!example', lambda message, args: 'Fixture reply: '+args)
        incoming = IncomingMessage('id','chat','viewer','!EXAMPLE test')
        reply = registry.dispatch(incoming, 'bot')
        self.assertEqual(reply.text, 'Fixture reply: test')
        self.assertEqual(reply.source_id, 'id')
        self.assertEqual(reply, registry.dispatch(incoming, 'bot'))
        self.assertIsNone(registry.dispatch(IncomingMessage('id','chat','bot','!example'), 'bot'))
        self.assertIsNone(registry.dispatch(IncomingMessage('id','chat','viewer','!unknown'), 'bot'))

    def test_invalid_or_duplicate_commands_and_invalid_replies(self):
        registry = CommandRegistry()
        for name in ['bad', '!Upper', '!a b', '!']:
            with self.assertRaises(ValueError):
                registry.register(name, lambda message, args: 'x')
        registry.register('!example', lambda message, args: 'x'*201)
        with self.assertRaises(ValueError):
            registry.register('!example', lambda message, args: 'x')
        with self.assertRaises(ValueError):
            registry.dispatch(IncomingMessage('id','chat','viewer','!example'), 'bot')


if __name__ == '__main__':
    unittest.main()
