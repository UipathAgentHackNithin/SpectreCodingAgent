"""
Scans all XAML files in a cloned repo and extracts structured metadata.
Used by the agent to build a repo summary for LLM file selection.
"""
import os
import xml.etree.ElementTree as ET
from typing import Optional

try:
    from .logger import get_logger
except ImportError:
    from logger import get_logger

log = get_logger("spectre.xaml_scanner")

_UIPATH_NS = "http://schemas.uipath.com/workflow/activities"
_XAML_NS = "http://schemas.microsoft.com/netfx/2009/xaml/activities"


def scan_repo_xamls(repo_path: str) -> list[dict]:
    """
    Walk all .xaml files in the repo and extract structured metadata from each.
    Returns a list of dicts, one per file.
    """
    results = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if not f.endswith(".xaml"):
                continue
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, repo_path)
            metadata = _extract_xaml_metadata(full_path, rel_path)
            if metadata:
                results.append(metadata)
    return results


def _extract_xaml_metadata(file_path: str, rel_path: str) -> Optional[dict]:
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
    except ET.ParseError as e:
        log.warning(f"Skipping malformed XAML {rel_path}: {e}")
        return None

    display_names = []
    activity_types = []
    catch_exception_types = []
    log_messages = []
    throw_messages = []
    selectors = []
    endpoints = []
    variable_names = []

    for elem in root.iter():
        tag = elem.tag
        # Strip namespace
        local = tag.split("}")[-1] if "}" in tag else tag
        attribs = elem.attrib

        # DisplayName
        dn = attribs.get("DisplayName")
        if dn:
            display_names.append(dn)

        # Activity type (local tag name, skip generic containers)
        if local not in ("Activity", "Sequence", "Members", "Property", "x:Members", "x:Property"):
            activity_types.append(local)

        # CatchBlock exception types
        if local == "Catch" or "CatchBlock" in local:
            exc = attribs.get("ExceptionType") or attribs.get("{http://schemas.microsoft.com/winfx/2006/xaml}TypeArguments")
            if exc:
                catch_exception_types.append(exc)

        # LogMessage — capture Error/Warn level messages
        if local == "LogMessage":
            level = attribs.get("Level", "")
            if level in ("Error", "Warn", "Warning"):
                msg = attribs.get("Message", "")
                if msg:
                    log_messages.append(msg[:300])

        # Throw / Rethrow
        if local in ("Throw", "Rethrow"):
            exc_expr = attribs.get("Exception", "")
            if exc_expr:
                throw_messages.append(exc_expr[:300])

        # Selectors — look for Selector attribute on any UI activity
        selector = attribs.get("Selector") or attribs.get("{http://schemas.uipath.com/workflow/activities}Selector")
        if selector:
            selectors.append(selector[:300])

        # REST/HTTP endpoints
        for key in ("Endpoint", "URL", "Uri", "url"):
            ep = attribs.get(key)
            if ep:
                endpoints.append(ep[:200])

        # Variable names from x:Members
        if local in ("Variable", "x:Property") or "Variable" in local:
            vname = attribs.get("Name") or attribs.get("{http://schemas.microsoft.com/winfx/2006/xaml}Name")
            if vname:
                variable_names.append(vname)

    return {
        "path": rel_path,
        "display_names": list(dict.fromkeys(display_names)),  # dedupe, preserve order
        "activity_types": list(dict.fromkeys(activity_types)),
        "catch_exception_types": list(dict.fromkeys(catch_exception_types)),
        "log_messages": log_messages,
        "throw_messages": throw_messages,
        "selectors": selectors,
        "endpoints": list(dict.fromkeys(endpoints)),
        "variable_names": list(dict.fromkeys(variable_names)),
    }


def build_repo_summary(scan_results: list[dict]) -> str:
    """
    Build a compact text summary of the repo for the LLM file-selection call.
    """
    lines = [f"Repository contains {len(scan_results)} XAML file(s):\n"]
    for f in scan_results:
        lines.append(f"--- FILE: {f['path']} ---")
        if f.get("size"):
            lines.append(f"  Size: {f['size']} bytes")
        if f.get("display_names"):
            lines.append(f"  Activities: {', '.join(f['display_names'][:15])}")
        if f.get("activity_types"):
            unique_types = [t for t in f["activity_types"] if t not in ("If", "Sequence", "Flowchart", "FlowDecision")]
            if unique_types:
                lines.append(f"  Activity types: {', '.join(unique_types[:10])}")
        if f.get("catch_exception_types"):
            lines.append(f"  Catches: {', '.join(f['catch_exception_types'])}")
        if f.get("log_messages"):
            lines.append(f"  Error/Warn logs: {' | '.join(f['log_messages'][:3])}")
        if f.get("throw_messages"):
            lines.append(f"  Throws: {' | '.join(f['throw_messages'][:2])}")
        if f.get("selectors"):
            lines.append(f"  Selectors: {' | '.join(f['selectors'][:2])}")
        if f.get("endpoints"):
            lines.append(f"  Endpoints: {', '.join(f['endpoints'][:3])}")
        lines.append("")
    return "\n".join(lines)
