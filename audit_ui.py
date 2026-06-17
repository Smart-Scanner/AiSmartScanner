import os
import re
import glob

def audit_templates():
    templates_dir = "templates"
    issues = []
    
    # Check for hardcoded colors
    color_regex = re.compile(r'color:\s*(#[0-9a-fA-F]{3,8})')
    bg_regex = re.compile(r'background:\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\))')
    style_regex = re.compile(r'style="([^"]+)"')
    
    for filepath in glob.glob(f"{templates_dir}/**/*.html", recursive=True):
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for i, line in enumerate(lines):
                # Hardcoded inline styles
                if 'style=' in line:
                    styles = style_regex.findall(line)
                    for style in styles:
                        if 'color:' in style or 'background-color:' in style or 'background:' in style:
                            issues.append({
                                'file': filepath,
                                'line': i+1,
                                'type': 'Hardcoded Inline Style',
                                'desc': f'Found inline color/bg: {style}'
                            })
                
                # Check for table without responsive wrapper
                if '<table' in line:
                    if '<div class="table-responsive">' not in ''.join(lines[max(0, i-2):i+1]) and 'overflow' not in ''.join(lines[max(0, i-2):i+1]):
                        issues.append({
                            'file': filepath,
                            'line': i+1,
                            'type': 'Table Responsiveness',
                            'desc': 'Table might not be wrapped in an overflow container, causing collapsing on mobile.'
                        })

    for issue in issues:
        print(f"[{issue['type']}] {issue['file']}:{issue['line']} - {issue['desc']}")

if __name__ == "__main__":
    audit_templates()
