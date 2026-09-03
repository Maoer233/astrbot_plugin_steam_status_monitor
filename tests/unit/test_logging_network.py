import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.shared.logging import format_exception, redact_sensitive, register_sensitive_values
from src.shared.network import configure_tls, get_ssl_ca_file, httpx_client_kwargs, requests_verify


class LoggingTests(unittest.TestCase):
    def test_redacts_query_keys_proxy_credentials_and_registered_secrets(self):
        register_sensitive_values("private-api-key")
        message = (
            "https://user:password@example.com/path?key=private-api-key&token=token-value "
            "private-api-key"
        )

        redacted = redact_sensitive(message)

        self.assertNotIn("password", redacted)
        self.assertNotIn("private-api-key", redacted)
        self.assertNotIn("token-value", redacted)
        self.assertIn("******", redacted)

    def test_format_exception_keeps_type_when_message_is_empty(self):
        empty_timeout = type("ConnectTimeout", (Exception,), {})("")
        self.assertEqual("ConnectTimeout", format_exception(empty_timeout))
        self.assertEqual("ValueError: boom", format_exception(ValueError("boom")))


class NetworkConfigTests(unittest.TestCase):
    def tearDown(self):
        configure_tls()

    def test_empty_ca_uses_default_verification(self):
        configure_tls()

        self.assertEqual("", get_ssl_ca_file())
        self.assertTrue(requests_verify())
        self.assertNotIn("verify", httpx_client_kwargs(None))

    def test_missing_ca_is_rejected(self):
        with self.assertRaises(ValueError):
            configure_tls("definitely-missing-ca.pem")

    def test_custom_ca_is_shared_by_http_clients(self):
        with tempfile.NamedTemporaryFile(suffix=".pem") as ca_file:
            with patch("src.shared.network.ssl.create_default_context", return_value=ssl.create_default_context()) as create:
                configure_tls(ca_file.name)

        create.assert_called_once_with(cafile=str(Path(ca_file.name).resolve()))
        self.assertEqual(str(Path(ca_file.name).resolve()), requests_verify())
        self.assertIsInstance(httpx_client_kwargs(None)["verify"], ssl.SSLContext)


if __name__ == "__main__":
    unittest.main()
