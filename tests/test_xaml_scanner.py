"""Tests for xaml_scanner — metadata extraction from XAML files."""
import os
import tempfile
import pytest

XAML_FULL = """\
<Activity x:Class="Process" xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"
  xmlns:ui="http://schemas.uipath.com/workflow/activities"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <Sequence DisplayName="Main Sequence">
    <ui:Click DisplayName="Click Login Button" Selector="&lt;wnd app='sap.exe' title='SAP Logon' /&gt;" />
    <ui:TypeInto DisplayName="Enter Username" />
    <TryCatch DisplayName="Try Login">
      <TryCatch.Catches>
        <Catch x:TypeArguments="s:SelectorNotFoundException" DisplayName="Catch SelectorNotFound">
          <ui:LogMessage DisplayName="Log Selector Error" Level="Error" Message="[&quot;Selector not found: login button&quot;]" />
        </Catch>
      </TryCatch.Catches>
    </TryCatch>
    <Throw DisplayName="Throw Business Error" Exception="[new BusinessRuleException(&quot;Invalid invoice number&quot;)]" />
    <ui:InvokeRESTService DisplayName="Call SAP API" Endpoint="https://api.sap.com/invoices" />
  </Sequence>
</Activity>"""

XAML_MINIMAL = """\
<Activity x:Class="Sub" xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <Sequence DisplayName="SubWorkflow">
    <Assign DisplayName="Set Variable" />
  </Sequence>
</Activity>"""

XAML_MALFORMED = "<Activity><Sequence><unclosed>"


def _write(tmp, filename, content, subdir=None):
    d = os.path.join(tmp, subdir) if subdir else tmp
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestScanRepoXamls:
    def test_returns_one_entry_per_xaml_file(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Process.xaml", XAML_FULL)
            _write(tmp, "Sub.xaml", XAML_MINIMAL)
            results = scan_repo_xamls(tmp)
        assert len(results) == 2

    def test_extracts_display_names(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Process.xaml", XAML_FULL)
            results = scan_repo_xamls(tmp)
        names = results[0]["display_names"]
        assert "Click Login Button" in names
        assert "Enter Username" in names

    def test_extracts_catch_exception_types(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Process.xaml", XAML_FULL)
            results = scan_repo_xamls(tmp)
        catches = results[0]["catch_exception_types"]
        assert any("SelectorNotFoundException" in c for c in catches)

    def test_extracts_error_log_messages(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Process.xaml", XAML_FULL)
            results = scan_repo_xamls(tmp)
        assert any("Selector not found" in m for m in results[0]["log_messages"])

    def test_extracts_throw_messages(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Process.xaml", XAML_FULL)
            results = scan_repo_xamls(tmp)
        assert any("Invalid invoice number" in t for t in results[0]["throw_messages"])

    def test_extracts_selector_strings(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Process.xaml", XAML_FULL)
            results = scan_repo_xamls(tmp)
        assert any("sap.exe" in s for s in results[0]["selectors"])

    def test_extracts_endpoint_urls(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Process.xaml", XAML_FULL)
            results = scan_repo_xamls(tmp)
        assert any("api.sap.com" in e for e in results[0]["endpoints"])

    def test_skips_hidden_directories(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Process.xaml", XAML_MINIMAL, subdir=".git/Framework")
            _write(tmp, "Main.xaml", XAML_MINIMAL)
            results = scan_repo_xamls(tmp)
        assert len(results) == 1
        assert ".git" not in results[0]["path"]

    def test_handles_malformed_xaml_gracefully(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Bad.xaml", XAML_MALFORMED)
            _write(tmp, "Good.xaml", XAML_MINIMAL)
            results = scan_repo_xamls(tmp)
        # Bad file skipped, good file included
        assert len(results) == 1
        assert "Good.xaml" in results[0]["path"]

    def test_returns_empty_list_when_no_xaml_files(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls
        with tempfile.TemporaryDirectory() as tmp:
            results = scan_repo_xamls(tmp)
        assert results == []

    def test_path_is_repo_relative(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Process.xaml", XAML_MINIMAL, subdir="Framework")
            results = scan_repo_xamls(tmp)
        assert not os.path.isabs(results[0]["path"])
        assert "Framework" in results[0]["path"]


class TestBuildRepoSummary:
    def test_summary_contains_file_paths(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls, build_repo_summary
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Process.xaml", XAML_FULL)
            results = scan_repo_xamls(tmp)
            summary = build_repo_summary(results)
        assert "Process.xaml" in summary

    def test_summary_contains_activity_names(self):
        from spectre_coding.xaml_scanner import scan_repo_xamls, build_repo_summary
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "Process.xaml", XAML_FULL)
            results = scan_repo_xamls(tmp)
            summary = build_repo_summary(results)
        assert "Click Login Button" in summary
