import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from app.trade_notifications import opening_evidence, format_admin_open, message_parts

class OpeningNotificationsTests(unittest.TestCase):
    def test_actual_levels_and_accounting_are_reported_without_credentials(self):
        evidence = opening_evidence(signal={'strategy_id':'mtf_simple','regime_id':'TREND','signal_reference_price':100,'private_key':'SECRET','last_candle_t_5m':1700000000000},plan={'approved_margin_usdc':3,'leverage':5,'private_key':'SECRET'},stop=99,target=102,protection={'sl_ok':True,'tp_ok':False,'token':'SECRET'})
        data=dict(symbol='SOL-PERP',direction='long',entry_price=100,qty=.15,notional_usdc=15,opened_at_ms=1700000030000,manager_started=True,admin_evidence=evidence)
        report=format_admin_open(123,'tester',data)
        self.assertNotIn('SECRET',report)
        self.assertIn('PnL al SL USDC: -0.15000000',report)
        self.assertIn('PnL al TP USDC: 0.30000000',report)
        self.assertIn('tp_ok: no',report)
        self.assertIn('30.000',report)
        self.assertIn('123:SOL-PERP:1700000030000',report)

    def test_missing_fields_are_not_claimed_as_confirmed(self):
        report=format_admin_open(1,'test',{'symbol':'BTC'})
        self.assertIn('sl_ok: no disponible',report)
        self.assertNotIn('PnL al SL USDC:',report)

    def test_unicode_chunks_preserve_all_details(self):
        text='🚀é\n'*5000
        parts=message_parts(text)
        self.assertEqual(''.join(p.split('\n',1)[1] for p in parts),text)
        self.assertTrue(all(len(p.encode('utf-16-le'))//2 < 4096 for p in parts))

    def test_admin_sender_sends_each_part_to_admin_only(self):
        tree=ast.parse(Path('app/trading_loop.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='send_admin_push_safe')
        node.returns=None
        for arg in node.args.args+node.args.kwonlyargs: arg.annotation=None
        env={'ADMIN_TELEGRAM_ID':99,'_admin_push_last_sent':{},'asyncio':asyncio,'tg_error':SimpleNamespace(RetryAfter=type('RetryAfter',(Exception,),{})),'log':lambda *a:None}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'sender','exec'),env)
        bot=SimpleNamespace(send_message=AsyncMock())
        text='evidence\n'*900
        asyncio.run(env['send_admin_push_safe'](SimpleNamespace(bot=bot),text))
        calls=bot.send_message.call_args_list
        self.assertEqual([c.kwargs['text'] for c in calls],message_parts(text))
        self.assertTrue(all(c.kwargs['chat_id']==99 for c in calls))
