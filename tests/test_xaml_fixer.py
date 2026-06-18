"""Tests for xaml_fixer — XAML discovery and LLM patch application."""
import os
import pytest
import tempfile
from unittest.mock import AsyncMock, patch

SAMPLE_XAML = """\
<Activity x:Class="Process">
  <Sequence DisplayName="Process">
    <ui:LogMessage DisplayName="Log Message Process Start" Level="Info" Message="[&quot;Started Process&quot;]" />
    <Throw Exception="[new SystemException(&quot;[Invoice Processing] Failed to process invoice UNKNOWN. &quot; + in_ErrorMessage)]" />
  </Sequence>
</Activity>"""


# ── find_primary_xaml ─────────────────────────────────────────────────────────

class TestFindPrimaryXaml:
    def test_prefers_framework_process_xaml(self):
        from spectre_coding.xaml_fixer import find_primary_xaml
        with tempfile.TemporaryDirectory() as tmp:
            fw_dir = os.path.join(tmp, "Framework")
            os.makedirs(fw_dir)
            fw_file = os.path.join(fw_dir, "Process.xaml")
            other = os.path.join(tmp, "Main.xaml")
            open(fw_file, "w").close()
            open(other, "w").close()
            result = find_primary_xaml(tmp)
            assert result == fw_file

    def test_falls_back_to_any_process_xaml(self):
        from spectre_coding.xaml_fixer import find_primary_xaml
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "Process.xaml")
            other = os.path.join(tmp, "Main.xaml")
            open(p, "w").close()
            open(other, "w").close()
            result = find_primary_xaml(tmp)
            assert result == p

    def test_falls_back_to_first_xaml_if_no_process_xaml(self):
        from spectre_coding.xaml_fixer import find_primary_xaml
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "Main.xaml")
            open(f, "w").close()
            result = find_primary_xaml(tmp)
            assert result == f

    def test_returns_none_when_no_xaml_files(self):
        from spectre_coding.xaml_fixer import find_primary_xaml
        with tempfile.TemporaryDirectory() as tmp:
            result = find_primary_xaml(tmp)
            assert result is None

    def test_skips_hidden_directories(self):
        from spectre_coding.xaml_fixer import find_primary_xaml
        with tempfile.TemporaryDirectory() as tmp:
            hidden = os.path.join(tmp, ".git", "Framework")
            os.makedirs(hidden)
            hidden_file = os.path.join(hidden, "Process.xaml")
            open(hidden_file, "w").close()
            result = find_primary_xaml(tmp)
            assert result is None


# ── apply_llm_fix ─────────────────────────────────────────────────────────────

class TestApplyLlmFix:
    def _make_repo(self, tmp: str, content: str = SAMPLE_XAML) -> str:
        fw = os.path.join(tmp, "Framework")
        os.makedirs(fw)
        path = os.path.join(fw, "Process.xaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return tmp

    @pytest.mark.asyncio
    async def test_applies_patch_when_llm_returns_valid_snippet(self):
        llm_response = {
            "can_fix": True,
            "reason": "",
            "original_snippet": "UNKNOWN",
            "replacement_snippet": "N/A",
            "explanation": "Replaced UNKNOWN with N/A for cleaner error messages",
            "confidence": "High",
        }
        with tempfile.TemporaryDirectory() as tmp:
            self._make_repo(tmp)
            with patch("spectre_coding.xaml_fixer.analyse_and_fix", AsyncMock(return_value=llm_response)):
                from spectre_coding.xaml_fixer import apply_llm_fix
                result = await apply_llm_fix(
                    repo_path=tmp, access_token="tok", base_url="http://x",
                    diagnosis="business rule", recommended_action="fix it",
                    process_name="3201 Invoice", transaction_id="TXN-1",
                )
        assert result["fixed"] is True
        assert result["original_snippet"] == "UNKNOWN"
        assert result["replacement_snippet"] == "N/A"
        assert result["llm_confidence"] == "High"

    @pytest.mark.asyncio
    async def test_file_is_actually_modified_on_disk(self):
        llm_response = {
            "can_fix": True,
            "reason": "",
            "original_snippet": "UNKNOWN",
            "replacement_snippet": "N/A",
            "explanation": "fix",
            "confidence": "High",
        }
        with tempfile.TemporaryDirectory() as tmp:
            self._make_repo(tmp)
            target = os.path.join(tmp, "Framework", "Process.xaml")
            with patch("spectre_coding.xaml_fixer.analyse_and_fix", AsyncMock(return_value=llm_response)):
                from spectre_coding.xaml_fixer import apply_llm_fix
                await apply_llm_fix(
                    repo_path=tmp, access_token="tok", base_url="http://x",
                    diagnosis="d", recommended_action="r",
                    process_name="3201 Invoice", transaction_id="TXN-1",
                )
            with open(target, "r", encoding="utf-8") as f:
                patched = f.read()
        assert "UNKNOWN" not in patched
        assert "N/A" in patched

    @pytest.mark.asyncio
    async def test_does_not_patch_when_snippet_not_in_xaml(self):
        llm_response = {
            "can_fix": True,
            "reason": "",
            "original_snippet": "THIS_DOES_NOT_EXIST_IN_XAML",
            "replacement_snippet": "something",
            "explanation": "fix",
            "confidence": "Medium",
        }
        with tempfile.TemporaryDirectory() as tmp:
            self._make_repo(tmp)
            with patch("spectre_coding.xaml_fixer.analyse_and_fix", AsyncMock(return_value=llm_response)):
                from spectre_coding.xaml_fixer import apply_llm_fix
                result = await apply_llm_fix(
                    repo_path=tmp, access_token="tok", base_url="http://x",
                    diagnosis="d", recommended_action="r",
                    process_name="3201 Invoice", transaction_id="TXN-1",
                )
        assert result["fixed"] is False
        assert result["can_fix"] is True  # LLM said yes, but snippet wasn't found verbatim
        assert "could not be located" in result["explanation"]

    @pytest.mark.asyncio
    async def test_returns_no_fix_when_llm_says_cannot_fix(self):
        llm_response = {
            "can_fix": False,
            "reason": "Fix requires external SAP credential rotation — out of scope for XAML change",
            "original_snippet": "",
            "replacement_snippet": "",
            "explanation": "",
            "confidence": "High",
        }
        with tempfile.TemporaryDirectory() as tmp:
            self._make_repo(tmp)
            with patch("spectre_coding.xaml_fixer.analyse_and_fix", AsyncMock(return_value=llm_response)):
                from spectre_coding.xaml_fixer import apply_llm_fix
                result = await apply_llm_fix(
                    repo_path=tmp, access_token="tok", base_url="http://x",
                    diagnosis="SAP credentials expired", recommended_action="Rotate credentials",
                    process_name="3201 Invoice", transaction_id="TXN-1",
                )
        assert result["fixed"] is False
        assert result["can_fix"] is False
        assert "SAP credential" in result["explanation"]

    @pytest.mark.asyncio
    async def test_returns_no_fix_when_no_xaml_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            # empty repo — no .xaml files
            from spectre_coding.xaml_fixer import apply_llm_fix
            result = await apply_llm_fix(
                repo_path=tmp, access_token="tok", base_url="http://x",
                diagnosis="d", recommended_action="r",
                process_name="3201 Invoice", transaction_id="TXN-1",
            )
        assert result["fixed"] is False
        assert "No XAML" in result["explanation"]
