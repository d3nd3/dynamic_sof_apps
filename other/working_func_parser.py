import re
import argparse
import os

MAX_CVAR_SIZE = 255

class QuakeScriptParser:
    """
    Improved QuakeScript parser with better packing efficiency and control flow optimization.
    Key fixes:
    1. Robust _parse_blocks method that correctly handles multiple functions and nested structures.
    2. Smarter control flow compilation that avoids creating atomic commands that are too large.
    3. Optimized packing algorithm that maximizes CVar usage.
    4. Enforces MAX_CVAR_SIZE on the CVar value in the 'set' command line.
    5. [NEW] Correctly uses brace-based syntax for control flow inlined in a function's main CVar.
    """

    def __init__(self, max_cvar_size=MAX_CVAR_SIZE):
        self.max_cvar_size = max_cvar_size
        self.comment_stripper_regex = re.compile(r'(".*?")|(\/\/.*)')
        self.control_keywords = ('sp_sc_flow_if', 'sp_sc_flow_while')
        self.autogen_counter = 0
        self.helper_cvars = []

    def _strip_comment(self, line: str) -> str:
        def replacer(match):
            return match.group(1) or ''
        return self.comment_stripper_regex.sub(replacer, line).strip()

    def escape_text(self, text: str) -> str:
        return text.replace('%', '%25').replace('"', '%22').replace('\n', '%0A')

    def _parse_blocks(self, line_iterator: iter) -> list:
        """
        [FIXED] A robust AST parser that correctly handles nested blocks,
        multiple functions, and modern brace styling. It no longer terminates
        prematurely when encountering a closing brace in the top-level scope.
        """
        nodes = []
        lines = list(line_iterator)
        i = 0
        
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue

            # This is not a block, just a simple command line
            if not line.startswith('function ') and not line.startswith(self.control_keywords):
                nodes.append(line)
                i += 1
                continue
            
            # --- Block Parsing Logic ---
            block_header = line
            block_type = 'function' if line.startswith('function ') else 'control'
            
            brace_pos = line.find('{')
            if brace_pos != -1:
                block_header = line[:brace_pos].strip()
                lines[i] = line[brace_pos + 1:].strip()
            else:
                i += 1
                if i >= len(lines) or lines[i].strip() != '{':
                    nodes.append(block_header)
                    continue
                i += 1

            body_lines = []
            brace_count = 1
            
            while i < len(lines):
                current_line = lines[i]
                brace_count += current_line.count('{')
                brace_count -= current_line.count('}')
                
                if brace_count == 0:
                    final_brace_idx = current_line.rfind('}')
                    body_lines.append(current_line[:final_brace_idx].strip())
                    remaining_line = current_line[final_brace_idx+1:].strip()
                    lines[i] = remaining_line 
                    if not remaining_line:
                        i += 1
                    break
                
                body_lines.append(current_line)
                i += 1
            else:
                print(f"Warning: Unmatched opening brace for block: {block_header}")
            
            body_nodes = self._parse_blocks(iter(body_lines))
            else_nodes = None
            
            if block_type == 'control' and block_header.startswith('sp_sc_flow_if'):
                current_line_remainder = lines[i].strip() if i < len(lines) else ""
                if current_line_remainder.startswith('else'):
                    else_body_lines, i = self._extract_else_block(lines, i)
                    else_nodes = self._parse_blocks(iter(else_body_lines))

            nodes.append([block_type, block_header, body_nodes, else_nodes])
        
        return nodes

    def _extract_else_block(self, lines: list, current_index: int) -> (list, int):
        i = current_index
        line = lines[i].strip()
        line = line[4:].strip()

        if line.startswith('{'):
            line = line[1:].strip()
        else:
            i += 1
            if i >= len(lines) or not lines[i].strip().startswith('{'):
                print("Warning: 'else' found without a following '{' block. Ignoring.")
                return [], i
            line = lines[i].strip()[1:].strip()
        
        lines[i] = line
        body_lines = []
        brace_count = 1
        
        while i < len(lines):
            current_line = lines[i]
            brace_count += current_line.count('{')
            brace_count -= current_line.count('}')

            if brace_count == 0:
                final_brace_idx = current_line.rfind('}')
                body_lines.append(current_line[:final_brace_idx].strip())
                lines[i] = current_line[final_brace_idx+1:].strip()
                if not lines[i]: i += 1
                return body_lines, i

            body_lines.append(current_line)
            i += 1
        
        print("Warning: Unmatched opening brace for 'else' block.")
        return body_lines, i

    def _compile_nodes_to_command(self, nodes: list, parent_func_name: str) -> str:
        """
        Compiles AST nodes into a single, semicolon-separated command string.
        This format uses quoted arguments for control flow (`if "..." "..."`) and
        is suitable for helper/body CVars.
        """
        if not nodes: return ""
        commands = []
        max_atomic_cmd_size = self.max_cvar_size - 80 

        for node in nodes:
            if isinstance(node, str):
                if node: commands.append(node)
                continue
            
            _, header, true_nodes, false_nodes = node
            true_cmd = self._compile_nodes_to_command(true_nodes, parent_func_name)
            false_cmd = self._compile_nodes_to_command(false_nodes, parent_func_name) if false_nodes else ""

            def create_helper(command_str, block_type):
                if not command_str: return "", None
                helper_name = f"f_{parent_func_name}_autogen_{self.autogen_counter}"
                self.autogen_counter += 1
                helper_cvars = self._pack_command_to_cvars(command_str, helper_name)
                if not helper_cvars:
                    print(f"FATAL: Failed to create helper CVar for {block_type} block in {parent_func_name}")
                    return None, None
                self.helper_cvars.extend(helper_cvars)
                return f"sp_sc_exec_cvar {helper_cvars[0][0]}", helper_name

            true_is_helper, false_is_helper = False, False
            potential_cmd_inlined = f'{header} "{true_cmd}" "{false_cmd}"'
            if len(self.escape_text(potential_cmd_inlined)) > max_atomic_cmd_size:
                if len(self.escape_text(true_cmd)) >= len(self.escape_text(false_cmd)):
                    true_cmd, _ = create_helper(true_cmd, "true"); true_is_helper = True
                else:
                    false_cmd, _ = create_helper(false_cmd, "false"); false_is_helper = True

            potential_cmd_one_helper = f'{header} "{true_cmd}" "{false_cmd}"'
            if len(self.escape_text(potential_cmd_one_helper)) > max_atomic_cmd_size:
                if not true_is_helper: true_cmd, _ = create_helper(true_cmd, "true")
                if not false_is_helper and false_cmd: false_cmd, _ = create_helper(false_cmd, "false")

            control_cmd = f'{header} "{true_cmd}" "{false_cmd}"' if false_cmd else f'{header} "{true_cmd}"'
            commands.append(control_cmd)
        
        return "; ".join(commands)
    
    # --- NEW METHOD ---
    def _compile_nodes_to_block_format(self, nodes: list, indent_level: int = 1) -> str:
        """
        Compiles AST nodes into a multi-line, brace-based command string.
        This format is required when a function's body is inlined into its
        main CVar. It does NOT create helpers, as it assumes the content is small.
        """
        output = []
        indent = "  " * indent_level
        for node in nodes:
            if isinstance(node, str):
                if node: output.append(f"{indent}{node}")
                continue

            _, header, true_nodes, false_nodes = node
            output.append(f"{indent}{header}")
            output.append(f"{indent}{{")
            output.append(self._compile_nodes_to_block_format(true_nodes, indent_level + 1))
            output.append(f"{indent}}}")

            if false_nodes:
                output.append(f"{indent}else")
                output.append(f"{indent}{{")
                output.append(self._compile_nodes_to_block_format(false_nodes, indent_level + 1))
                output.append(f"{indent}}}")
        
        return "\n".join(output)

    def _get_set_command_overhead(self, cvar_name: str) -> int:
        return len(f'set {cvar_name} ""')

    def _pack_command_to_cvars(self, command: str, base_name: str) -> list:
        """
        [CORRECTED] Packs a string of commands into chained CVars. Now includes a
        post-processing step to merge the final chunk if it was split off
        unnecessarily due to conservative link-space reservation.
        """
        if not command: return []
        atomic_commands = [cmd for cmd in re.split(r'; (?=(?:[^"]*"[^"]*")*[^"]*$)', command) if cmd]
        if not atomic_commands: return []

        chunks, current_chunk = [], ""
        sep = self.escape_text('; ')
        link_overhead = len(self.escape_text(f'; sp_sc_exec_cvar {base_name}_99'))

        for part in atomic_commands:
            set_overhead_check = self._get_set_command_overhead(f"{base_name}_99")
            escaped_part = self.escape_text(part)
            if len(escaped_part) > self.max_cvar_size - set_overhead_check - link_overhead:
                print(f"FATAL: A single atomic command is too large to fit in any CVar: {part[:80]}...")
                return None

            set_overhead = self._get_set_command_overhead(f"{base_name}_{len(chunks)}")
            max_size_with_link = self.max_cvar_size - set_overhead - link_overhead
            
            if not current_chunk:
                current_chunk = escaped_part
            elif len(current_chunk) + len(sep) + len(escaped_part) <= max_size_with_link:
                current_chunk += sep + escaped_part
            else:
                chunks.append(current_chunk)
                current_chunk = escaped_part
        
        if current_chunk:
            chunks.append(current_chunk)
            
        # --- START OF THE FIX ---
        # Post-processing merge pass. If the last chunk was created unnecessarily
        # due to the reserved link space, this will merge it back.
        if len(chunks) > 1:
            last_chunk = chunks[-1]
            penultimate_chunk = chunks[-2]
            
            # Calculate overhead for the cvar that would hold the merged content.
            # Its index is `len(chunks) - 2`.
            penultimate_cvar_name = f"{base_name}_{len(chunks) - 2}"
            set_overhead = self._get_set_command_overhead(penultimate_cvar_name)
            
            # Max size for a FINAL chunk (which has no outgoing link).
            max_final_chunk_size = self.max_cvar_size - set_overhead
            
            if len(penultimate_chunk) + len(sep) + len(last_chunk) <= max_final_chunk_size:
                # It fits! Merge them.
                chunks[-2] = penultimate_chunk + sep + last_chunk
                chunks.pop() # Remove the now-redundant last chunk.
        # --- END OF THE FIX ---

        cvars = []
        for i, chunk in enumerate(chunks):
            cvar_name = f"{base_name}_{i}"
            set_overhead = self._get_set_command_overhead(cvar_name)
            content = chunk
            if i < len(chunks) - 1:
                content += self.escape_text(f"; sp_sc_exec_cvar {base_name}_{i+1}")
            if len(content) + set_overhead > self.max_cvar_size:
                print(f"FATAL: Packer error. Content chunk is too large for {cvar_name}")
                return None
            cvars.append((cvar_name, content))
        return cvars

    # --- UPDATED METHOD ---
    def _pack_function_ast(self, func_ast: list) -> list:
        self.helper_cvars = []
        self.autogen_counter = 0
        
        if not func_ast or func_ast[0] != 'function': return []
        
        _, func_header, func_body_nodes, _ = func_ast
        func_name = re.search(r'function\s+([a-zA-Z0-9_]*)', func_header).group(1)
        
        if not func_body_nodes:
            return [(f"f_{func_name}_0", self.escape_text(f"{func_header}\n{{\n}}"))]

        # First, generate the semicolon-based command string to estimate size and for use in body CVars.
        body_command_string = self._compile_nodes_to_command(func_body_nodes, func_name)
        if body_command_string is None: # A fatal error occurred in a helper.
            print(f"FATAL: Could not compile body for {func_name}")
            return None

        shell_prefix = self.escape_text(f"{func_header}\n{{\n")
        shell_suffix = self.escape_text("\n}")
        main_cvar_name = f"f_{func_name}_0"
        main_cvar_overhead = self._get_set_command_overhead(main_cvar_name)
        available_space = self.max_cvar_size - main_cvar_overhead - len(shell_prefix) - len(shell_suffix)
        
        cvars = []
        
        # Now, generate the brace-based, multi-line format for potential inlining.
        body_block_string = self._compile_nodes_to_block_format(func_body_nodes)
        body_block_escaped = self.escape_text(body_block_string)

        # DECISION POINT: Can the brace-based format be inlined?
        if len(body_block_escaped) <= available_space:
            # YES: Use the brace-based format directly in the main CVar.
            full_shell = shell_prefix + body_block_escaped + shell_suffix
            cvars.append((main_cvar_name, full_shell))
        else:
            # NO: The body is too large. Pack the semicolon-based command string
            # into helper body CVars and use an exec command.
            body_base_name = f"f_{func_name}_body"
            body_cvars = self._pack_command_to_cvars(body_command_string, body_base_name)
            if not body_cvars:
                print(f"Failed to pack body for function {func_name}")
                return None
            
            exec_cmd = self.escape_text(f"  sp_sc_exec_cvar {body_cvars[0][0]}\n")
            if len(shell_prefix + exec_cmd + shell_suffix) > self.max_cvar_size - main_cvar_overhead:
                print(f"Shell CVar too large even with exec command for {func_name}")
                return None
            
            shell = shell_prefix + exec_cmd + shell_suffix
            cvars.append((main_cvar_name, shell))
            cvars.extend(body_cvars)
        
        cvars.extend(self.helper_cvars)
        return cvars

    def generate_cfg_output(self, cvars: list) -> str:
        # (This method remains unchanged)
        output_lines = ['//--- Generated by QuakeScriptParser ---//',
                        '// This file is auto-generated. Do not edit manually.', '']
        for name, val in cvars:
            final_line = f'set {name} "{val}"'
            if len(final_line) > self.max_cvar_size:
                 print(f"FATAL: Generated line for CVar '{name}' exceeds MAX_CVAR_SIZE ({len(final_line)} > {self.max_cvar_size}).")
            output_lines.append(final_line)
            output_lines.append(f'sp_sc_cvar_unescape {name} {name}')
            output_lines.append('')
        entry_points, seen_bases = [], set()
        for cvar_name, _ in cvars:
            if "_autogen_" in cvar_name or "_body_" in cvar_name: continue
            base = cvar_name.rsplit('_', 1)[0]
            if base not in seen_bases:
                entry_points.append(cvar_name); seen_bases.add(base)
        if entry_points:
            output_lines.append('// --- Entry Points ---')
            output_lines.append('// Use these commands in your autoexec.cfg or script initializers to load the functions.')
            for entry_point in entry_points:
                output_lines.append(f'sp_sc_func_load_cvar {entry_point}')
        return '\n'.join(output_lines)

    def parse_and_pack_script(self, content: str) -> list:
        # (This method remains unchanged)
        all_cvars = []
        lines = [self._strip_comment(line) for line in content.split('\n')]
        top_level_nodes = self._parse_blocks(iter(lines))
        function_nodes = [n for n in top_level_nodes if isinstance(n, list) and n[0] == 'function']
        if not function_nodes:
             print("No functions found in the script."); return []
        for node in function_nodes:
            name_match = re.search(r'function\s+([a-zA-Z0-9_]+)', node[1])
            # if not name_match:
            #     print(f"  - Ignoring unnamed function: '{node[1].strip()}'"); continue
            if name_match is None:
                func_name = "anonymous"
            else:
                func_name = name_match.group(1)
            print(f"  - Processing function '{func_name}'...")
            function_cvars = self._pack_function_ast(node)
            if function_cvars is None:
                print(f"    ! Packing failed for '{func_name}'. Aborting generation."); return []
            all_cvars.extend(function_cvars)
            if function_cvars: print(f"    > Generated {len(function_cvars)} CVar(s) for '{func_name}'.")
        return all_cvars


def main():
    # (main function remains unchanged)
    parser = argparse.ArgumentParser(description="Parse a .func script file into a Quake 2 .cfg file with optimized CVar packing.", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("input_file", help="Path to the input .func file.")
    parser.add_argument("-o", "--output_file", help="Path to the output .cfg file. Defaults to input file with .cfg extension.")
    parser.add_argument("--max-cvar-size", type=int, default=MAX_CVAR_SIZE, help=f"Set the maximum CVar size (default: {MAX_CVAR_SIZE}).")
    args = parser.parse_args()
    input_path = args.input_file
    output_path = args.output_file or os.path.splitext(input_path)[0] + ".cfg"

    if not os.path.exists(input_path):
        print(f"Error: Input file not found at '{input_path}'")
        return

    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()
    print("Starting script parsing...")
    parser_instance = QuakeScriptParser(max_cvar_size=args.max_cvar_size)
    cvars = parser_instance.parse_and_pack_script(content)
    if not cvars: print("No CVars generated. This could be due to a parsing error or an empty input file."); return
    seen, unique_cvars = set(), []
    for name, val in cvars:
        if name not in seen: unique_cvars.append((name,val)); seen.add(name)
    cfg = parser_instance.generate_cfg_output(unique_cvars)
    with open(output_path, 'w', encoding='utf-8') as f: f.write(cfg)
    print(f"\nSuccess! Generated CFG at '{output_path}' with {len(unique_cvars)} unique CVars.")

if __name__ == '__main__':
    main()