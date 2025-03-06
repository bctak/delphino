import subprocess
import re
import sys
import os

def get_glibc_functions():
    """Get a list of all symbols provided by glibc, including all symbol types."""
    try:
        result = subprocess.run(["nm", "-D", "/lib/x86_64-linux-gnu/libc.so.6"], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        functions = set()
        for line in result.stdout.split("\n"):
            parts = line.split()
            if len(parts) >= 3:  # Include all symbols without filtering by type
                functions.add(parts[2])
        return functions
    except Exception as e:
        print(f"Error retrieving glibc functions: {e}")
        return set()

def get_code_body_start(ast_output, file_path):
    """Find the first occurrence of '<file_path' in Clang AST dump to determine where the code body starts."""
    file_marker = f"<{file_path}"
    lines = ast_output.split("\n")
    
    for i, line in enumerate(lines):
        if file_marker in line:
            return i  # Return the first occurrence index
    
    return None  # Return None if file marker is not found

def extract_function_calls_with_clang(file_path):
    """Extract function calls from C source file using Clang AST dump."""
    try:
        result = subprocess.run(["clang", "-Xclang", "-ast-dump", "-fsyntax-only", file_path], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        code_body_start = get_code_body_start(result.stdout, file_path)

        if code_body_start is None:
            print("Warning: Could not determine the code body start position.")
            return set()
        
        function_calls = set()
        user_functions = set()

        lines = result.stdout.split("\n")[code_body_start:]
        for i in range(len(lines)):
            if "CallExpr" in lines[i]:
                for j in range(i + 1, min(i + 5, len(lines))):  # Check next few lines
                    if "DeclRefExpr" in lines[j]:
                        match = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[j])
                        if match:
                            function_calls.add(match.group(1))
                            break
            elif "FunctionDecl" in lines[i] and "implicit used" not in lines[i]:
                match = re.search(r"FunctionDecl\s+[^\']+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*'", lines[i])
                if match:
                    user_functions.add(match.group(1))
        
        return user_functions, function_calls
    except Exception as e:
        print(f"Error parsing C code with Clang: {e}")
        return set()

def extract_glibc_functions_from_c_code(file_path):
    """Extract glibc function calls from a given C source file using Clang AST."""
    user_functions, all_functions = extract_function_calls_with_clang(file_path)
    glibc_functions = get_glibc_functions()
    used_glibc_functions = all_functions.intersection(glibc_functions)
    return used_glibc_functions

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python3 '+ sys.argv[0] +' <C_FILE>')
        sys.exit(1)
    
    c_file = sys.argv[1]  # Get file from command-line argument
    
    if not os.path.isfile(c_file):
        print(f"Error: File '{c_file}' not found.")
        sys.exit(1)
    
    glibc_calls = extract_glibc_functions_from_c_code(c_file)
    print("Glibc functions used in the code:")
    for func in sorted(glibc_calls):
        print(func)