"""Exercise the complete PC upload workflow without network/hardware."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

COMMON = Path(__file__).resolve().parents[2] / "examples/platformio/common"
SPEC = importlib.util.spec_from_file_location("user_upload", COMMON / "upload.py")
UPLOAD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPLOAD)


class Session:
    def __init__(self, rc):
        self.rc = rc
        self.commands = []
        self.copies = []
        self.closed = False

    def run(self, cmd):
        self.commands.append(cmd)
        return 0 if cmd == "true" else self.rc

    def put(self, local, remote):
        self.copies.append((local, remote))
        return 0

    def close(self):
        self.closed = True


class UploadTest(unittest.TestCase):
    def test_nonroot_upload_streams_to_helper_and_propagates_agent_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            elf = Path(tmp) / "firmware.elf"
            elf.write_bytes(b"test ELF")
            for result in (0, 2, 3, 4):
                session = Session(result)
                with self.subTest(result=result), patch.object(UPLOAD.duo_ssh, "USER", "beitlab"), patch.object(UPLOAD.duo_ssh, "Session", return_value=session):
                    self.assertEqual(UPLOAD.main(["upload.py", str(elf)]), result)
                self.assertTrue(session.closed)
                self.assertEqual(len(session.copies), 1)
                remote = session.copies[0][1]
                self.assertTrue(remote.startswith(".playground-upload-"))
                self.assertNotIn("/", remote)
                command = session.commands[-1]
                self.assertIn("sudo -n /usr/libexec/playground/rtos-control load < " + remote, command)
                self.assertIn("rm -f -- " + remote, command)
                self.assertIn("exit $rc", command)

    def test_explicit_legacy_root_uses_agent_path(self):
        with patch.object(UPLOAD.duo_ssh, "USER", "root"):
            self.assertEqual(UPLOAD.duo_ssh.agent_command("load", "/tmp/a.elf"), "rtos-agent load /tmp/a.elf")

    def test_mailbox_uses_noninteractive_user_session_and_preserves_arguments(self):
        from unittest.mock import Mock
        for arguments, command in ((["0x41", "1"], "bl_send 0x41 1"),
                                   (["$(id)"], "bl_send '$(id)'")):
            session = Mock()
            session.run.return_value = 2
            with self.subTest(arguments=arguments), patch.object(UPLOAD.duo_ssh, "USER", "beitlab"), patch.object(UPLOAD.duo_ssh, "Session", return_value=session):
                self.assertEqual(UPLOAD.duo_ssh.main(["duo_ssh.py", "bl_send", *arguments]), 2)
            session.run.assert_called_once_with(command)
            session.admin.assert_not_called()
            session.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
