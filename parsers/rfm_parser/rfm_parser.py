import argparse
import hashlib
import os
from typing import List, Tuple

MAX_CVAR_SIZE = 255


class RmfCvarNamer:
    """
    Generates short, unique, and deterministic CVar base names for RFM files.

    - Seeded by a label (e.g., file basename) for deterministic results
    - 4-character hex hash yields 65,536 possible bases
    - Prefix 'm_' denotes menu/RMF content
    """

    def __init__(self, seed: str, hash_len: int = 4) -> None:
        self.hash = hashlib.md5(seed.encode("utf-8")).hexdigest()[:hash_len]

    def get_base(self) -> str:
        return f"m_{self.hash}"


class RmfParser:
    """
    Parser and packer for .rfm (SOF1-like menu layout) files into CVars.

    Requirements implemented:
    - Do NOT include <stm> or </stm> in any CVar content
    - Keep whole <...> tags intact within a single CVar; never split a tag
    - Text content between tags may be split across CVars
    - Chain CVars with <includecvar next_cvar> so an external loader can start with the first
    - Ensure each generated `set NAME "VALUE"` line is <= MAX_CVAR_SIZE
    - Use percent-encoding for %, " and newlines to be compatible with sp_sc_cvar_unescape
    """

    def __init__(self, max_cvar_size: int = MAX_CVAR_SIZE) -> None:
        self.max_cvar_size = max_cvar_size

    def escape_text(self, text: str) -> str:
        return text.replace('%', '%25').replace('"', '%22').replace('\n', '%0A')

    def _get_set_overhead(self, cvar_name: str) -> int:
        # Measures length of: set {cvar_name} ""
        return len(f'set {cvar_name} ""')

    def _tokenize(self, content: str) -> List[Tuple[str, str]]:
        """
        Tokenize the file into ('tag', '<...>') and ('text', '...') parts.
        - Honors quotes inside tags so '>' within quotes doesn't end the tag
        - Drops <stm> and </stm> tags entirely
        - If <stm>... </stm> are present, include ONLY content strictly between them
          (exclude any characters before the first <stm> and after the closing </stm>).
          If no <stm> tag is present, fall back to including the whole file (minus any literal <stm> tags).
        """
        tokens: List[Tuple[str, str]] = []
        i = 0
        n = len(content)
        lower_content = content.lower()
        has_stm = '<stm>' in lower_content
        inside_stm = False

        while i < n:
            if content[i] == '<':
                j = i + 1
                in_quote = False
                while j < n:
                    c = content[j]
                    if c == '"':
                        in_quote = not in_quote
                    if c == '>' and not in_quote:
                        j += 1
                        break
                    j += 1
                if j > n:
                    j = n
                tag_text = content[i:j]
                tag_lower = tag_text.lower().strip()

                if tag_lower == '<stm>':
                    inside_stm = True
                    # Do not emit the <stm> tag itself
                elif tag_lower == '</stm>':
                    # Close stm region; do not emit
                    inside_stm = False
                else:
                    # Emit tag if we are inside the stm region, or if there is no stm in the file at all
                    if inside_stm or not has_stm:
                        tokens.append(('tag', tag_text))
                i = j
            else:
                j = i
                while j < n and content[j] != '<':
                    j += 1
                text = content[i:j]
                if text:
                    if inside_stm or not has_stm:
                        tokens.append(('text', text))
                i = j
        return tokens

    def _split_text_to_fit(self, current_escaped_len: int, current_raw: str, text: str, max_with_link: int) -> Tuple[str, str]:
        """
        Split 'text' so that current_raw + chosen_prefix fits within max_with_link when escaped.
        Returns (prefix_to_add, remainder_text).
        """
        if not text:
            return '', ''
        # Fast path: try everything
        escaped_all = self.escape_text(current_raw + text)
        if len(escaped_all) <= max_with_link:
            return text, ''
        # Binary search for the largest prefix length that fits
        lo, hi = 1, len(text)
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = current_raw + text[:mid]
            if len(self.escape_text(candidate)) <= max_with_link:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best == 0:
            return '', text
        return text[:best], text[best:]

    def _pack_tokens_to_chunks(self, tokens: List[Tuple[str, str]], base_name: str) -> List[str]:
        """
        Greedy packer that:
        - Keeps whole tags together
        - Splits text as needed
        - Reserves room for a placeholder link `<includecvar {base}_999>\n` in all chunks (worst-case)
        """
        if not tokens:
            return []

        # Reserve pessimistically for name length growth (up to _999).
        placeholder_name = f"{base_name}_999"
        link_placeholder_raw = f"<includecvar {placeholder_name}>\n"
        link_placeholder_escaped_len = len(self.escape_text(link_placeholder_raw))
        set_overhead_placeholder_len = self._get_set_overhead(placeholder_name)
        max_with_link = self.max_cvar_size - set_overhead_placeholder_len - link_placeholder_escaped_len

        if max_with_link <= 0:
            raise RuntimeError(
                f"Insufficient capacity for any content (max={self.max_cvar_size})."
            )

        chunks: List[str] = []
        current_raw_parts: List[str] = []

        def current_raw_str() -> str:
            return ''.join(current_raw_parts)

        for token_type, value in tokens:
            if token_type == 'tag':
                # Whole tag must fit in the current chunk or a new chunk
                candidate_raw = current_raw_str() + value
                if len(self.escape_text(candidate_raw)) <= max_with_link:
                    current_raw_parts.append(value)
                else:
                    # If current chunk has some content, flush it first
                    if current_raw_parts:
                        chunks.append(current_raw_str())
                        current_raw_parts = []
                        candidate_raw = value
                    # Now the tag must fit into an empty chunk with link reserved
                    if len(self.escape_text(candidate_raw)) > max_with_link:
                        raise RuntimeError(
                            f"A single tag exceeds allowed size: {value[:120]}"
                        )
                    current_raw_parts.append(value)
            else:  # text
                remaining = value
                while remaining:
                    prefix, rem = self._split_text_to_fit(
                        current_escaped_len=len(self.escape_text(current_raw_str())),
                        current_raw=current_raw_str(),
                        text=remaining,
                        max_with_link=max_with_link,
                    )
                    if prefix:
                        current_raw_parts.append(prefix)
                        remaining = rem
                    else:
                        # No room left in this chunk; flush and continue in a new chunk
                        if current_raw_parts:
                            chunks.append(current_raw_str())
                            current_raw_parts = []
                        else:
                            # Nothing in chunk and still can't fit a single char: impossible under our budgets
                            # But to be safe, move one char anyway (will raise later if exceeds)
                            current_raw_parts.append(remaining[0])
                            remaining = remaining[1:]
        if current_raw_parts:
            chunks.append(current_raw_str())
        return chunks

    def parse_and_pack(self, content: str, seed_label: str) -> List[Tuple[str, str]]:
        tokens = self._tokenize(content)
        namer = RmfCvarNamer(seed_label)
        base = namer.get_base()

        chunks = self._pack_tokens_to_chunks(tokens, base)
        if not chunks:
            return []

        cvars: List[Tuple[str, str]] = []
        for i, raw_chunk in enumerate(chunks):
            cvar_name = f"{base}_{i}"
            raw_with_links = raw_chunk
            if i < len(chunks) - 1:
                raw_with_links += f"<includecvar {base}_{i + 1}>\n"
            escaped_value = self.escape_text(raw_with_links)
            set_overhead = self._get_set_overhead(cvar_name)
            total_line_len = set_overhead + len(escaped_value)
            if total_line_len > self.max_cvar_size:
                raise RuntimeError(
                    f"Generated CVar '{cvar_name}' exceeds MAX_CVAR_SIZE ({total_line_len} > {self.max_cvar_size})."
                )
            cvars.append((cvar_name, escaped_value))
        return cvars

    def generate_cfg_output(self, cvars: List[Tuple[str, str]]) -> str:
        lines: List[str] = [
            '//--- Generated by RmfParser ---//',
            '// This file is auto-generated. Do not edit manually.',
            '',
        ]
        for name, val in cvars:
            lines.append(f'set {name} "{val}"')
            lines.append(f'sp_sc_cvar_unescape {name} {name}')
            lines.append('')

        if cvars:
            entry = cvars[0][0]
            lines.append('// --- Entry Point ---')
            lines.append('// In your outer <stm> ... </stm> menu, use:')
            lines.append(f'//   <includecvar {entry}>')
        return '\n'.join(lines)


def main() -> None:
    cli = argparse.ArgumentParser(
        description=(
            "Parse a .rfm (SOF1-like markup) file into a .cfg that stores the menu"
            " as chained CVars using <includecvar>."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    cli.add_argument("input_file", help="Path to the input .rmf file.")
    cli.add_argument(
        "-o",
        "--output_file",
        help="Path to the output .cfg file. Defaults to input file with .cfg extension.",
    )
    cli.add_argument(
        "--max-cvar-size",
        type=int,
        default=MAX_CVAR_SIZE,
        help=f"Set the maximum CVar size (default: {MAX_CVAR_SIZE}).",
    )
    args = cli.parse_args()

    input_path = args.input_file
    output_path = args.output_file or os.path.splitext(input_path)[0] + ".cfg"

    if not os.path.exists(input_path):
        print(f"Error: Input file not found at '{input_path}'")
        return

    with open(input_path, "r", encoding="utf-8") as f:
        content = f.read()

    print("Starting RFM parsing...")
    parser = RmfParser(max_cvar_size=args.max_cvar_size)
    seed_label = os.path.basename(input_path)
    try:
        cvars = parser.parse_and_pack(content, seed_label=seed_label)
    except RuntimeError as e:
        print(f"FATAL: {e}")
        return

    if not cvars:
        print("No CVars generated. The input might be empty after removing <stm> wrappers.")
        return

    # Deduplicate by name, preserving order
    seen = set()
    unique: List[Tuple[str, str]] = []
    for name, val in cvars:
        if name in seen:
            continue
        seen.add(name)
        unique.append((name, val))

    cfg_text = parser.generate_cfg_output(unique)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(cfg_text)

    print(f"\nSuccess! Generated CFG at '{output_path}' with {len(unique)} CVars.")


if __name__ == "__main__":
    main()
