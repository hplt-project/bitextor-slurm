#!/usr/bin/env python3
"""
Fix language code in alignment file: replace 'no' with 'nn' (Norwegian Nynorsk)
"""

import gzip
import re
import sys

def fix_lang_code(input_path, output_path=None):
    """
    Replace language code 'no' with 'nn' in a gzipped XML alignment file.

    Args:
        input_path: Path to input .gz file
        output_path: Path to output .gz file (if None, adds .fixed before .gz)
    """
    if output_path is None:
        output_path = input_path.replace('.gz', '.fixed.gz')

    print(f"Reading: {input_path}")
    print(f"Writing: {output_path}")

    with gzip.open(input_path, 'rt', encoding='utf-8') as f_in:
        content = f_in.read()

    # Replace language code 'no' with 'nn' in common XML patterns:
    # - fromDoc/toDoc attributes referencing files like "no/..."
    # - lang attributes like lang="no"
    # - Any path references containing /no/ or starting with no/

    replacements = [
        # File path references: fromDoc="no/..." or toDoc="no/..."
        (r'(fromDoc|toDoc)="no/', r'\1="nn/'),
        # Language attributes: lang="no"
        (r'lang="no"', 'lang="nn"'),
        # Generic attribute containing just "no" as language
        (r'="no"', '="nn"'),
    ]

    total_changes = 0
    for pattern, replacement in replacements:
        content, count = re.subn(pattern, replacement, content)
        if count > 0:
            print(f"  Pattern '{pattern}': {count} replacements")
            total_changes += count

    print(f"Total replacements: {total_changes}")

    with gzip.open(output_path, 'wt', encoding='utf-8') as f_out:
        f_out.write(content)

    print("Done!")
    return output_path


if __name__ == '__main__':
    input_file = '/fs/bil0/bhaddow/dochplt/bitexting_v3-clean/combined/alignments_deduped/en-no.alignments.gz'
    output_file = '/fs/bil0/bhaddow/dochplt/bitexting_v3-clean/combined/alignments_deduped/en-nn.alignments.gz'

    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]

    fix_lang_code(input_file, output_file)
