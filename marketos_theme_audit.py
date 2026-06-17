"""
MarketOS UI Forensic Audit — Steps 1, 2 & 3 Combined
Generates:
  1. Theme Audit Report (hardcoded colors per file)
  2. SVG/Icon Audit (fill/stroke hardcoded values)
  3. CSS Variable Coverage % (token vs hardcoded ratio)
"""
import os, re, glob, json

TEMPLATES_DIR = "templates"
CSS_DIR = "static/css"
ARTIFACT_DIR = r"C:\Users\91971\.gemini\antigravity-ide\brain\9255473f-643e-4a4d-9c00-e02fa637e3e5"

# ── Patterns ──────────────────────────────────────────────────────
# Hardcoded color in CSS properties
HARDCODED_RE = re.compile(
    r'(color|background(?:-color)?|border(?:-color)?|box-shadow|fill|stroke)'
    r'\s*:\s*'
    r'(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\))',
    re.IGNORECASE
)

# Token-based color (var(--xxx))
TOKEN_RE = re.compile(
    r'(color|background(?:-color)?|border(?:-color)?|box-shadow|fill|stroke)'
    r'\s*:\s*'
    r'var\(--[^)]+\)',
    re.IGNORECASE
)

# SVG-specific hardcoded fill/stroke attributes
SVG_ATTR_RE = re.compile(
    r'(fill|stroke)\s*=\s*["\']'
    r'(#[0-9a-fA-F]{3,8}|white|#fff(?:fff)?|rgba?\([^)]+\))'
    r'["\']',
    re.IGNORECASE
)

# Whitelist — these are acceptable hardcoded values (transparent, none, inherit, currentColor, 0)
WHITELIST = {'transparent', 'none', 'inherit', 'currentColor', 'currentcolor', '0'}

def scan_files():
    """Scan all HTML templates and CSS files."""
    files = []
    for ext in ['html', 'css']:
        files.extend(glob.glob(f"{TEMPLATES_DIR}/**/*.{ext}", recursive=True))
    files.extend(glob.glob(f"{CSS_DIR}/**/*.css", recursive=True))
    return files

def audit():
    files = scan_files()

    hardcoded_report = {}   # file -> [{line, property, value, context}]
    svg_report = {}         # file -> [{line, attr, value, context}]
    total_hardcoded = 0
    total_token = 0

    for filepath in files:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except:
            continue

        for i, line in enumerate(lines):
            # Count token-based references
            token_matches = TOKEN_RE.findall(line)
            total_token += len(token_matches)

            # Count and log hardcoded references
            hard_matches = HARDCODED_RE.findall(line)
            for prop, value in hard_matches:
                if value.strip().lower() in WHITELIST:
                    continue
                total_hardcoded += 1
                if filepath not in hardcoded_report:
                    hardcoded_report[filepath] = []
                ctx = line.strip()[:80]
                hardcoded_report[filepath].append({
                    'line': i + 1,
                    'property': prop,
                    'value': value,
                    'context': ctx
                })

            # SVG attribute audit
            svg_matches = SVG_ATTR_RE.findall(line)
            for attr, value in svg_matches:
                if value.strip().lower() in WHITELIST:
                    continue
                if filepath not in svg_report:
                    svg_report[filepath] = []
                ctx = line.strip()[:80]
                svg_report[filepath].append({
                    'line': i + 1,
                    'attr': attr,
                    'value': value,
                    'context': ctx
                })

    # ── Calculate coverage ──
    total_refs = total_token + total_hardcoded
    coverage_pct = round((total_token / total_refs) * 100, 2) if total_refs > 0 else 100.0

    # ── Write report ──
    report_path = os.path.join(ARTIFACT_DIR, "theme_audit_report.md")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("# MarketOS Theme Audit Report\n\n")
        f.write("## Step 3 — CSS Variable Coverage\n\n")
        f.write(f"| Metric | Count |\n")
        f.write(f"|--------|-------|\n")
        f.write(f"| Total Color References | {total_refs} |\n")
        f.write(f"| Token-Based (`var(--*)`) | {total_token} |\n")
        f.write(f"| Hardcoded (`#hex`, `rgb`, `rgba`) | {total_hardcoded} |\n")
        f.write(f"| **Theme Coverage** | **{coverage_pct}%** |\n\n")
        target_status = "✅ PASS" if coverage_pct >= 98 else "❌ FAIL — needs cleanup"
        f.write(f"> Target: > 98% — **{target_status}**\n\n")
        f.write("---\n\n")

        # Step 1 — Hardcoded Color Report
        f.write("## Step 1 — Hardcoded Color Audit\n\n")
        f.write(f"**Total Hardcoded Colors**: {total_hardcoded} across {len(hardcoded_report)} files\n\n")
        for filepath in sorted(hardcoded_report.keys()):
            items = hardcoded_report[filepath]
            f.write(f"### `{filepath}`\n")
            f.write(f"**Count**: {len(items)}\n\n")
            f.write("| Line | Property | Value | Context |\n")
            f.write("|------|----------|-------|---------|\n")
            for item in items:
                ctx = item['context'].replace('|', '\\|')
                f.write(f"| {item['line']} | `{item['property']}` | `{item['value']}` | `{ctx}` |\n")
            f.write("\n")

        f.write("---\n\n")

        # Step 2 — SVG/Icon Audit
        f.write("## Step 2 — SVG/Icon Visibility Audit\n\n")
        total_svg = sum(len(v) for v in svg_report.values())
        f.write(f"**Total Hardcoded SVG fill/stroke**: {total_svg} across {len(svg_report)} files\n\n")
        if total_svg == 0:
            f.write("> ✅ No hardcoded SVG fill/stroke attributes found.\n\n")
        else:
            for filepath in sorted(svg_report.keys()):
                items = svg_report[filepath]
                f.write(f"### `{filepath}`\n")
                f.write(f"**Count**: {len(items)}\n\n")
                f.write("| Line | Attribute | Value | Context |\n")
                f.write("|------|-----------|-------|---------|\n")
                for item in items:
                    ctx = item['context'].replace('|', '\\|')
                    f.write(f"| {item['line']} | `{item['attr']}` | `{item['value']}` | `{ctx}` |\n")
                f.write("\n")

    print(f"Report written to: {report_path}")
    print(f"\n=== CSS Variable Coverage ===")
    print(f"Total References: {total_refs}")
    print(f"Token Based:      {total_token}")
    print(f"Hardcoded:        {total_hardcoded}")
    print(f"Coverage:         {coverage_pct}%")
    print(f"SVG Hardcoded:    {total_svg}")

if __name__ == "__main__":
    audit()
