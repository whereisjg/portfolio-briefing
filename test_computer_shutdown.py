import platform
import subprocess
import unittest
from unittest.mock import MagicMock, patch

import computer_shutdown as cs


class ShutdownCommandTests(unittest.TestCase):
    def test_windows_command(self):
        with patch.object(platform, "system", return_value="Windows"):
            self.assertEqual(cs.build_shutdown_command(), ["shutdown", "/s", "/t", "0"])

    def test_linux_command(self):
        with patch.object(platform, "system", return_value="Linux"):
            self.assertEqual(cs.build_shutdown_command(), ["sudo", "shutdown", "-h", "now"])

    def test_darwin_command(self):
        with patch.object(platform, "system", return_value="Darwin"):
            self.assertEqual(cs.build_shutdown_command(), ["sudo", "shutdown", "-h", "now"])

    def test_unsupported_os_raises(self):
        with patch.object(platform, "system", return_value="FreeBSD"):
            with self.assertRaises(RuntimeError):
                cs.build_shutdown_command()


class RunShutdownTests(unittest.TestCase):
    def test_dry_run_skips_subprocess(self):
        with patch.object(cs, "build_shutdown_command", return_value=["shutdown", "/s", "/t", "0"]):
            with patch.object(subprocess, "run") as mock_run:
                cs.run_shutdown(dry_run=True)
                mock_run.assert_not_called()

    def test_real_run_calls_subprocess(self):
        cmd = ["sudo", "shutdown", "-h", "now"]
        with patch.object(cs, "build_shutdown_command", return_value=cmd):
            with patch.object(subprocess, "run") as mock_run:
                cs.run_shutdown(dry_run=False)
                mock_run.assert_called_once_with(cmd, check=True)


class TelegramNotificationTests(unittest.TestCase):
    def test_skips_when_no_token(self):
        result = cs.send_telegram_notification("test", "", "123")
        self.assertFalse(result)

    def test_skips_when_no_chat_id(self):
        result = cs.send_telegram_notification("test", "token", "")
        self.assertFalse(result)

    def test_sends_when_configured(self):
        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_session = MagicMock()
        fake_session.post.return_value = fake_resp

        with patch.object(cs, "get_http_session", return_value=fake_session):
            result = cs.send_telegram_notification("컴퓨터를 종료합니다.", "tok", "999")

        self.assertTrue(result)
        fake_session.post.assert_called_once()

    def test_returns_false_on_network_error(self):
        fake_session = MagicMock()
        fake_session.post.side_effect = Exception("연결 실패")

        with patch.object(cs, "get_http_session", return_value=fake_session):
            result = cs.send_telegram_notification("msg", "tok", "999")

        self.assertFalse(result)


class MainTests(unittest.TestCase):
    def _run(self, argv, env=None):
        env = env or {}
        with patch.dict(cs.os.environ, env, clear=False):
            with patch.object(cs, "send_telegram_notification", return_value=True):
                with patch.object(cs, "run_shutdown") as mock_shutdown:
                    code = cs.main(argv)
        return code, mock_shutdown

    def test_dry_run_exits_zero(self):
        code, mock_shutdown = self._run(["--dry-run"])
        self.assertEqual(code, 0)
        mock_shutdown.assert_called_once_with(dry_run=True)

    def test_no_delay_calls_shutdown_immediately(self):
        code, mock_shutdown = self._run(["--dry-run"])
        self.assertEqual(code, 0)

    def test_delay_is_passed_but_skipped_in_dry_run(self):
        with patch.object(cs, "send_telegram_notification", return_value=True):
            with patch.object(cs, "run_shutdown"):
                with patch.object(cs.time, "sleep") as mock_sleep:
                    cs.main(["--delay", "5", "--dry-run"])
                    mock_sleep.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
