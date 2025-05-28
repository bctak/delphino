import subprocess
import re
import sys
import os
from graphviz import Digraph
import argparse
from itertools import combinations

#control constant
NORMAL_CONTROL = 0
IF_CONTROL = 0b1
SWITCH_CONTROL = 0b10
WHILE_CONTROL = 0b100
FOR_CONTROL = 0b1000
DO_WHILE_CONTROL = 0b10000
BREAK_CONTROL = 0b100000
CONTINUE_CONTROL = 0b1000000
RETURN_CONTROL = 0b10000000

FOR_DEVELOPMENT = 1

MAX_SINGLE_CONTROL_FLOW_COUNT = 100

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
    
def calls_library_function(func_name, user_functions, function_calls, visited):
    """
    Returns True if func_name (or anything it transitively calls) calls any library function.
    """
    if func_name not in function_calls:
        # If there is no call history for this function, assume it does not call any library function
        return False
    
    if func_name in visited:
        return False  # Skip already visited functions to avoid infinite recursion

    visited.add(func_name)

    for callee in function_calls[func_name]:
        if callee not in user_functions:
            return True  # A library function call is detected
        if calls_library_function(callee, user_functions, function_calls, visited):
            return True  # A library function call is detected in a nested callee

    return False

def extract_function_not_call_function(file_path):
    """Extract the function call graph from a C source file using Clang AST dump."""
    try:
        result = subprocess.run(["clang", "-Xclang", "-ast-dump", "-fsyntax-only", file_path], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Find the start position of the main code body
        code_body_start = get_code_body_start(result.stdout, file_path)
        if code_body_start is None:
            print("Warning: Could not determine the code body start position.")
            return {}

        user_functions, all_functions = extract_function_calls_with_clang(file_path)  # List of user-defined functions
        function_calls = {}  # Stores call relationships (caller -> [callee1, callee2, ...])
        current_function = None  # Currently analyzed function

        lines = result.stdout.split("\n")[code_body_start:] # Analyze only the body part

        for i in range(len(lines)):
            line = lines[i]

            # Identify which function we're currently inside (using FunctionDecl)
            if "FunctionDecl" in line:
                match = re.search(r"FunctionDecl\s+[^\']+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*'", line)
                if match:
                    current_function = match.group(1)
                    if current_function in user_functions:
                        function_calls[current_function] = []  # Initialize call graph entry

            # Detect function calls (using CallExpr followed by DeclRefExpr)
            elif "CallExpr" in line and current_function:
                call_depth = get_first_alpha_or_angle_index(line)
                for j in range(i + 1, min(i + 20, len(lines))):  # Check a few lines after CallExpr
                    if "DeclRefExpr" in lines[j]:
                        match = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[j])
                        if match:
                            called_function = match.group(1)
                            if called_function in all_functions or called_function in user_functions:  # 사용자 정의 함수만 추적
                                function_calls[current_function].append(called_function)
                            if "clone" == called_function:
                                for k in range(j + 1, min(j + 20, len(lines))):  # Check a few more lines after clone
                                    if "DeclRefExpr" in lines[k]:
                                        match2 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match2:
                                            cloned_function = match2.group(1)
                                            if cloned_function in all_functions or cloned_function in user_functions:
                                                function_calls[current_function].append(called_function)
                                            break
                            if "pthread_create" == called_function:
                                for k in range(j + 1, min(j + 20, len(lines))):  # Check a few more lines after pthread_create
                                    if "DeclRefExpr" in lines[k]:
                                        match3 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match3:
                                            pthread_create_function = match3.group(1)
                                            if pthread_create_function in all_functions or pthread_create_function in user_functions:
                                                function_calls[current_function].append(called_function)
                                            break
                        break
        
        user_functions_not_call = set()

        for user_func in user_functions:
            visited = set()
            if not calls_library_function(user_func, user_functions, function_calls, visited):
                user_functions_not_call.add(user_func)
        
        return user_functions_not_call
    except Exception as e:
        print(f"Error extracting function call graph: {e}")
        return {}

def get_first_alpha_or_angle_index2(line):
    """Find the index of the first alphabetic character (A-Z, a-z) in a given line."""
    match = re.search(r"[a-zA-Z]", line)
    return match.start() if match else float('inf')  # Return a large value if no alphabet found


def get_first_alpha_or_angle_index(line):
    """
    Find the index of the first alphabetic character (A-Z, a-z) or '<' in a given line.
    Returns the index of whichever comes first. If neither is found, returns a large value.
    """
    alpha_match = re.search(r"[a-zA-Z]", line)
    angle_index = line.find('<')

    alpha_index = alpha_match.start() if alpha_match else float('inf')
    angle_index = angle_index if angle_index != -1 else float('inf')

    return min(alpha_index, angle_index)

def get_first_backtick_index(line):
    """Find the index of the first backtick (`) character in a given line."""
    match = re.search(r"`", line)
    return match.start() if match else float('inf')  # Return a large value if no backtick found

def print_error(s,lines,i):
    print(s+'!!!')
    print(lines[i])
    print()
    print_line_count = 5
    for j in range(0,print_line_count):
        if i - print_line_count + j >= 0:
            print(lines[i-print_line_count+j])
    for j in range(0,print_line_count):
        if i + j < len(lines):
            print(lines[i+j])
    exit(1)

def print_for_debug(lines,i,n):
    print(lines[i])
    print()
    for j in range(0, n):
        print(lines[i- (int)(n/2)+ j])

def print_error_for_make_function(error_msg):
    print(error_msg)
    exit(1)

def make_graph_using_gui(call_graph_matrix_list,call_graph_function_pos_list,caller_list):
    if len(call_graph_matrix_list) == len(call_graph_function_pos_list) and len(call_graph_function_pos_list) == len(caller_list):
        for i in range(0,len(call_graph_function_pos_list)):
            call_graph_matrix = call_graph_matrix_list[i]
            call_graph_function_pos = call_graph_function_pos_list[i]
            caller = caller_list[i]
            adj_matrix = []
            node_names = []
            
            # Build adjacency matrix and list of function names
            for function_name, function_call_matrix in call_graph_matrix.items():
                node_names.append(function_name)
                adj_matrix.append(function_call_matrix)

            # Create a Graphviz graph
            dot = Digraph(format='pdf')
            dot.attr(rankdir='TB', size='8,5')   # Top-to-Bottom direction, set graph size

            # Add nodes
            for name in node_names:
                dot.node(name, shape='ellipse', style='filled', fillcolor='lightblue',penwidth='2.5')

            # Add edges
            num_nodes = len(adj_matrix)
            for i in range(num_nodes):
                for j in range(num_nodes):
                    if adj_matrix[i][j] != 0:
                        dot.edge(node_names[i], node_names[j])

            # Save the graph as a PDF file
            dot.render(str(caller), cleanup=True)
    else:
        print_error_for_make_function('length error')
    
def make_graph_using_gui_use_list(adj_matrix_name,adj_matrix,output_file_name=None): 
    node_names = adj_matrix_name
    
    # Create a Graphviz graph
    dot = Digraph(format='pdf')
    dot.attr(rankdir='TB', size='8,5')  # Top-to-Bottom direction, set graph size

    # Add nodes
    for name in node_names:
        dot.node(name, shape='ellipse', style='filled', fillcolor='lightblue',penwidth='2.5')

    # Add edges
    num_nodes = len(adj_matrix)
    for i in range(num_nodes):
        for j in range(num_nodes):
            if adj_matrix[i][j] != 0:
                dot.edge(node_names[i], node_names[j])

    # Save the graph as a PDF file
    if output_file_name == None:
        dot.render('FINAL_GRAPH', cleanup=True)  # Creates FINAL_GRAPH.pdf
    else:
        dot.render(output_file_name, cleanup=True)  # Creates output_file_name.pdf

def extract_if_depth(file_path):
    """Extract the function call graph from a C source file using Clang AST dump."""
    try:
        result = subprocess.run(["clang", "-Xclang", "-ast-dump", "-fsyntax-only", file_path], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Find the start position of the code body
        code_body_start = get_code_body_start(result.stdout, file_path)
        if code_body_start is None:
            print("Warning: Could not determine the code body start position.")
            return {}

        user_functions, all_functions = extract_function_calls_with_clang(file_path)  # List of user-defined functions
        function_calls = {}  # Dictionary to store call relationships (caller -> [callee1, callee2, ...])
        current_function = None  # The function currently being analyzed

        lines = result.stdout.split("\n")[code_body_start:]  # Analyze only the body part

        current_depth = 0
        function_stack = []
        function_call_depth = []
        current_call_depth = 0

        has_else_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
        if_first_ongoing = 0     # Tracks when IfStmt is first detected, used to capture the condition part
        if_conditional_ongoing = 0
        if_conditional_depth = 0  # Used to detect the depth of the if-statement condition; matches when depth resets
        if_conditional_depth_list = []
        return_ongoing_depth_list = []

        if_ongoing_depth = []
        current_has_else = 0
        if_level = 0
        if_level_else_if_list = []  # Used to represent else-if blocks in a wide (flat) structure
        if_level_else_if_list.append(0)
        if_level_else_if_list_not_append_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
        if_new_check = 0
        if_level_not_plus_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT

        conditional_operator_first_ongoing = 0
        conditional_operator_ongoing = 0
        conditional_operator_ongoing_list = []
        first_conditional_operator_depth = 0
        first_conditional_operator_depth_list = []
        conditional_ongoing_depth = []
        conditional_if_level_plus = 0

        if_level_else_if_list_not_append_conditional = 0

        # Variables for handling loops

        while_new_check = 0
        while_level = 0
        while_depth_list = []
        while_depth = 0
        while_ongoing = 0
        while_ongoing_depth = []
        while_first_ongoing = 0

        for_depth_list = []
        for_conditional_list =[0] * MAX_SINGLE_CONTROL_FLOW_COUNT
        for_depth = 0
        for_ongoing = 0
        for_ongoing_depth = []
        for_first_ongoing = 0

        do_while_new_check = 0
        do_while_level = 0
        do_while_ongoing_depth = []
        do_while_first_ongoing = 0
        do_while_depth_list = []
        do_while_depth = 0

        continue_stat = 0
        break_stat = 0

        #break_stat 1 means break appear anyhere
        #break_stat 2 means break appear in switch-case
        #break_stat 3 means case-> break-> case
        #if break_stat > 0 but not for, while, switch-case ... means previous information remained so discard this information

        switch_ongoing_depth = []
        switch_depth_list = []
        switch_depth = 0
        switch_ongoing = 0
        switch_level = 0
        switch_case_list = []
        switch_case_list.append(0)
        switch_not_append = 0
        switch_new_check = 0
        switch_first_ongoing = 0
        switch_break = 0

        control_flow_list = []
        control_flow_re_check = 0

        control_flow_iteration_lambda_function = []
        control_flow_iteration_lambda_function_pos = 0
        for i in range(0,MAX_SINGLE_CONTROL_FLOW_COUNT):
            control_flow_iteration_lambda_function.append('iteration lambda function' + str(i))

        
        lines.append('End of File!!')   #if return does not exist
        for i in range(len(lines)):
            line = lines[i]
            current_depth = get_first_alpha_or_angle_index(line)

            # Logic for handling multiple function calls on a single line
            if function_call_depth:  
                while current_depth <= function_call_depth[len(function_call_depth)-1]:
                    if function_stack and current_function:
                        function_calls[current_function].append(function_stack.pop())
                        function_call_depth.pop()
                        if function_stack:
                            if function_stack[-1][14] == 'clone' or function_stack[-1][14] == 'pthread_create':
                                temp_function_stack = function_calls[current_function].pop()
                                function_calls[current_function].append(function_stack.pop())
                                function_calls[current_function].append(temp_function_stack)
                    else:
                        print_error('function_stack empty or current_function empty',lines,i)
                    if not function_call_depth:
                        break    

            while control_flow_list:
                temp_control_flow = control_flow_list[-1]
                if temp_control_flow == IF_CONTROL:
                    if if_ongoing_depth:
                        if current_depth <= if_ongoing_depth[len(if_ongoing_depth)-1]:
                            if if_level <= 0:
                                print_error('if_level error1',lines,i)
                            function_calls[current_function].append(('end_info','if',if_level,if_new_check))
                            if_level_not_plus_list[if_level] = 0
                            if_level_else_if_list_not_append_list[if_level] = 0
                            if_level -= 1
                            if_ongoing_depth.pop()
                            if_level_else_if_list.pop()
                            control_flow_re_check = 1
                            control_flow_list.pop()
                            if if_conditional_depth_list:
                                if_conditional_depth_list.pop()
                            else:
                                print('empty!!',if_ongoing_depth,if_conditional_depth_list)
                            if if_new_check >= 1000:
                                if_new_check = 0
                            if_new_check += 1
                            if not if_level_else_if_list:
                                print_error('if_level_else_if_list error1 !!!!',lines,i)

                    if conditional_ongoing_depth:
                        if current_depth <= conditional_ongoing_depth[-1]:
                            if if_level <= 0:
                                print_error('if_level error3',lines,i)
                            function_calls[current_function].append(('end_info','conditional',if_level,if_new_check))
                            if_level -= 1
                            conditional_ongoing_depth.pop()
                            if_level_else_if_list.pop()
                            first_conditional_operator_depth_list.pop()
                            conditional_operator_ongoing_list.pop()
                            control_flow_re_check = 1
                            control_flow_list.pop()
                            if_level_else_if_list_not_append_conditional = 0
                            conditional_operator_ongoing = 0
                            if if_new_check >= 1000:
                                if_new_check = 0
                            if_new_check += 1
  
                elif temp_control_flow == WHILE_CONTROL:
                    if while_ongoing_depth:
                        if current_depth <= while_ongoing_depth[-1]:
                            if while_level <= 0:
                                print_error('while level error4',lines,i)
                            function_calls[current_function].append(('end_info','while',while_level,while_new_check))
                            if control_flow_iteration_lambda_function_pos > 0:
                                control_flow_iteration_lambda_function_pos -= 1
                            else:
                                print_error('control_flow_iteration_lambda_function_pos < 0',lines,i)
                            while_level -= 1
                            if while_new_check > 1000:
                                while_new_check = 0
                            while_new_check += 1
                            while_ongoing_depth.pop()   
                            while_depth_list.pop()
                            control_flow_re_check = 1
                            control_flow_list.pop()

                elif temp_control_flow == FOR_CONTROL:
                    if for_ongoing_depth:
                        if current_depth <= for_ongoing_depth[-1]:
                            if while_level <= 0:
                                print_error('while level error5',lines,i)
                            function_calls[current_function].append(('end_info','for',while_level,while_new_check))
                            for_conditional_list[while_level] = 0
                            if control_flow_iteration_lambda_function_pos > 0:
                                control_flow_iteration_lambda_function_pos -= 1
                            else:
                                print_error('control_flow_iteration_lambda_function_pos < 0',lines,i)                           
                            while_level -= 1
                            if while_new_check > 1000:
                                while_new_check = 0
                            while_new_check += 1
                            for_ongoing_depth.pop()   
                            for_depth_list.pop()
                            control_flow_re_check = 1
                            control_flow_list.pop()

                elif temp_control_flow == DO_WHILE_CONTROL:
                    if do_while_ongoing_depth:
                        if current_depth <= do_while_ongoing_depth[-1]:
                            if do_while_level <= 0:
                                print_error('while level error6',lines,i)
                            function_calls[current_function].append(('end_info','do_while',do_while_level,do_while_new_check))
                            if control_flow_iteration_lambda_function_pos > 0:
                                control_flow_iteration_lambda_function_pos -= 1
                            else:
                                print_error('control_flow_iteration_lambda_function_pos < 0',lines,i)                            
                            do_while_level -= 1
                            do_while_ongoing_depth.pop()   
                            do_while_depth_list.pop()
                            if do_while_new_check > 1000:
                                do_while_new_check = 0
                            do_while_new_check += 1
                            control_flow_re_check = 1
                            control_flow_list.pop()

                elif temp_control_flow == SWITCH_CONTROL:
                    if switch_ongoing_depth:
                        if current_depth <= switch_ongoing_depth[-1]:
                            if switch_level <= 0:
                                print_error('switch level error',lines,i)
                            function_calls[current_function].append(('end_info','switch',switch_level,switch_new_check))
                            switch_level -= 1
                            switch_ongoing_depth.pop()   
                            switch_depth_list.pop()
                            control_flow_re_check = 1
                            control_flow_list.pop()
                            if switch_break == 1:
                                switch_break = 0
                            if len(switch_case_list) >= 1:
                                switch_case_list.pop()
                            else:
                                print_error("switch case pop error")

                elif temp_control_flow == RETURN_CONTROL:
                    if return_ongoing_depth_list:
                        if current_depth <= return_ongoing_depth_list[-1]:
                            function_calls[current_function].append(('end_info','return',1))
                            return_ongoing_depth_list.pop()
                            control_flow_list.pop()
                            control_flow_re_check = 1
                else:
                    print_error('error!!!',lines,i)
                if control_flow_re_check == 1:
                    control_flow_re_check = 0
                else:
                    break
            # End of control flow termination handling

            # iteration statment start
            if while_depth_list:
                while_depth = while_depth_list[-1]
            else:
                while_depth = -1

            if "WhileStmt" in line:
                while_first_ongoing = 1
                temp_while_current_depth = get_first_alpha_or_angle_index(line)
                while_ongoing_depth.append(temp_while_current_depth)

            
            if while_first_ongoing > 0:
                if while_first_ongoing == 2:
                    while_first_ongoing = 0
                    if_conditional_depth = get_first_alpha_or_angle_index(line)
                    while_depth_list.append(if_conditional_depth)
                    while_ongoing = 1
                    if while_new_check >= 1000:
                        while_new_check = 0
                    while_new_check += 1
                    while_level += 1
                    function_calls[current_function].append(('start_info','while conditional',while_level,while_new_check))
                    function_calls[current_function].append([current_call_depth,i,if_level,if_level_else_if_list[if_level],if_new_check,switch_level,switch_case_list[switch_level],switch_new_check,while_level,while_new_check,do_while_level,do_while_new_check,break_stat,continue_stat,control_flow_iteration_lambda_function[control_flow_iteration_lambda_function_pos]])
                    control_flow_iteration_lambda_function_pos += 1

                else:
                    while_first_ongoing = 2

            if while_ongoing > 0:
                if while_ongoing == 2:
                    temp_current_depth = get_first_backtick_index(line)
                    if temp_current_depth + 2 == while_depth:
                        function_calls[current_function].append(('end_info','while conditional',while_level,while_new_check))
                        function_calls[current_function].append(('start_info','while',while_level,while_new_check))
                        control_flow_list.append(WHILE_CONTROL)
                        while_ongoing = 0
                else:
                    while_ongoing = 2   

            #for statement start
            if for_depth_list:          
                for_depth = for_depth_list[-1]
            else:
                for_depth = -1

            if "ForStmt" in line:
                for_first_ongoing = 1
                temp_for_current_depth = get_first_alpha_or_angle_index(line)
                for_ongoing_depth.append(temp_for_current_depth)

            if for_first_ongoing > 0:
                if for_first_ongoing == 2:
                    for_first_ongoing = 0
                    temp_for_current_depth = get_first_alpha_or_angle_index(line)
                    for_depth_list.append(temp_for_current_depth)
                    for_depth = temp_for_current_depth
                    for_ongoing = 1
                else:
                    for_first_ongoing = 2

            if for_ongoing > 0:
                temp_current_alpa_depth = get_first_alpha_or_angle_index(line)
                if for_ongoing == 2:                                 
                    temp_current_depth = get_first_backtick_index(line)
                    if temp_current_depth + 2 == for_depth:
                        function_calls[current_function].append(('end_info','for conditional second',while_level,while_new_check))
                        function_calls[current_function].append(('start_info','for',while_level,while_new_check))
                        control_flow_list.append(FOR_CONTROL)
                        for_ongoing = 0
                else:
                    while_level += 1
                    for_ongoing = 2
                if temp_current_alpa_depth == for_depth:
                    if for_conditional_list[while_level] == 2:
                        for_conditional_list[while_level] = 3
                        if while_new_check >= 1000:
                            while_new_check = 0
                        while_new_check += 1
                        function_calls[current_function].append(('start_info','for conditional first',while_level,while_new_check)) 
                        function_calls[current_function].append([current_call_depth,i,if_level,if_level_else_if_list[if_level],if_new_check,switch_level,switch_case_list[switch_level],switch_new_check,while_level,while_new_check,do_while_level,do_while_new_check,break_stat,continue_stat,control_flow_iteration_lambda_function[control_flow_iteration_lambda_function_pos]])
                        control_flow_iteration_lambda_function_pos += 1
                    elif for_conditional_list[while_level] == 3:
                        for_conditional_list[while_level] = 4
                        function_calls[current_function].append(('end_info','for conditional first',while_level,while_new_check))     
                        function_calls[current_function].append(('start_info','for conditional second',while_level,while_new_check))
                    else:
                        for_conditional_list[while_level] += 1
            
            #do-while start
            if do_while_depth_list:
                do_while_depth = do_while_depth_list[-1]
            else:
                do_while_depth = -1

            if "DoStmt" in line:
                do_while_first_ongoing = 1
                temp_do_while_current_depth = get_first_alpha_or_angle_index(line)
                do_while_ongoing_depth.append(temp_do_while_current_depth)
            
            if do_while_first_ongoing > 0:
                if do_while_first_ongoing == 2:
                    do_while_first_ongoing = 0
                    temp_do_while_current_depth = get_first_alpha_or_angle_index(line)
                    do_while_depth_list.append(temp_do_while_current_depth)
                    do_while_level += 1
                    if do_while_new_check >= 1000:
                        do_while_new_check = 0
                    do_while_new_check += 1
                    function_calls[current_function].append(('start_info','do_while',do_while_level,do_while_new_check))
                    function_calls[current_function].append([current_call_depth,i,if_level,if_level_else_if_list[if_level],if_new_check,switch_level,switch_case_list[switch_level],switch_new_check,while_level,while_new_check,do_while_level,do_while_new_check,break_stat,continue_stat,control_flow_iteration_lambda_function[control_flow_iteration_lambda_function_pos]])
                    control_flow_iteration_lambda_function_pos += 1

                    control_flow_list.append(DO_WHILE_CONTROL)
                else:
                    do_while_first_ongoing = 2
            
            if do_while_depth > 0:
                temp_do_while_current_depth = get_first_backtick_index(line)
                if temp_do_while_current_depth + 2 == do_while_depth:           #do while conditional start
                    function_calls[current_function].append(('start_info','do_while conditional',do_while_level,do_while_new_check))     
            

            # End of loop block
            # Start of ternary conditional expression

            if "ConditionalOperator" in line:
                conditional_operator_first_ongoing = 1
                temp_conditional_current_depth = get_first_alpha_or_angle_index(line)
                conditional_ongoing_depth.append(temp_conditional_current_depth)
                conditional_if_level_plus = 0
                if if_level_else_if_list_not_append_conditional == 0:
                    if_level_else_if_list.append(0)
                else:
                    if_level_else_if_list_not_append_conditional = 0
                if if_new_check >= 1000:
                    if_new_check = 0
                if_new_check += 1
            
            if first_conditional_operator_depth_list:
                first_conditional_operator_depth = first_conditional_operator_depth_list[-1]

            if conditional_operator_ongoing_list:
                conditional_operator_ongoing = conditional_operator_ongoing_list[-1]

            if conditional_operator_first_ongoing > 0:
                if conditional_operator_first_ongoing == 2:
                    conditional_operator_first_ongoing = 0
                    first_conditional_operator_depth = get_first_alpha_or_angle_index(line)
                    first_conditional_operator_depth_list.append(first_conditional_operator_depth)
                    conditional_operator_ongoing = 1
                else:
                    conditional_operator_first_ongoing = 2

            if conditional_operator_ongoing > 0:
                if conditional_operator_ongoing == 2:
                    if current_depth == first_conditional_operator_depth:
                        if conditional_if_level_plus == 0:
                            if_level += 1
                            function_calls[current_function].append(('start_info','conditional',if_level,if_new_check))
                            control_flow_list.append(IF_CONTROL)
                            conditional_if_level_plus = 1
                        conditional_operator_ongoing = 3
                        conditional_operator_ongoing_list[-1] = 3

                elif conditional_operator_ongoing == 3:
                    if current_depth == first_conditional_operator_depth:
                        if if_level_else_if_list and len(if_level_else_if_list) > if_level:
                            if_level_else_if_list[if_level] += 1
                            function_calls[current_function].append(('start_info','else',if_level,if_new_check))
                        conditional_operator_ongoing = 0
                else:
                    conditional_operator_ongoing = 2
                    conditional_operator_ongoing_list.append(conditional_operator_ongoing)


            # normal if statement
            if if_conditional_depth_list:
                if_conditional_depth = if_conditional_depth_list[-1]    
            else:
                if_conditional_depth = -1

            if has_else_list[if_level] == 1 and if_level == len(if_conditional_depth_list):
                temp_if_end_depth = get_first_backtick_index(line)
                if temp_if_end_depth + 2 == if_conditional_depth:
                    if if_level <= 0:
                        print_error('if_level error2',lines,i)
                    # The first index corresponds to if_level 1 (i.e., actual level starts from 1)
                    if if_level_else_if_list and len(if_level_else_if_list) > if_level:
                        if_level_else_if_list[if_level] += 1
                    else:
                        print_error('if_level_else_if_list error2 !!!!',lines,i)
                    if "IfStmt" in line:
                        if_ongoing_depth.pop()
                        if_level_not_plus_list[if_level] = 1
                        function_calls[current_function].append(('start_info','else if',if_level,if_new_check))
                        if_level_else_if_list_not_append_list[if_level] = 1
                    else:
                        function_calls[current_function].append(('start_info','else',if_level,if_new_check))
                    has_else_list[if_level] = 0

            if "IfStmt" in line:
                if if_level_else_if_list_not_append_list[if_level] == 0:
                    if_level_else_if_list.append(0)
                    if if_new_check >= 1000:
                        if_new_check = 0
                    if_new_check += 1
                else:
                    if_level_else_if_list_not_append_list[if_level] = 0
                if "has_else" in line:
                    if if_level_not_plus_list[if_level] == 1:
                        has_else_list[if_level] = 1
                    else: # Indicates a new 'if' statement
                        current_has_else = 1
                
                if_first_ongoing = 1
                temp_if_current_depth = get_first_alpha_or_angle_index(line)
                if_ongoing_depth.append(temp_if_current_depth)
            
            if if_first_ongoing > 0:
                if if_first_ongoing == 2:
                    if_first_ongoing = 0
                    temp_if_conditional_depth = get_first_alpha_or_angle_index(line)
                    if if_level_not_plus_list[if_level] == 1:
                        if_conditional_depth_list.pop()
                    if_conditional_depth_list.append(temp_if_conditional_depth)
                    if_conditional_ongoing = 1
                else:
                    if_first_ongoing = 2

            if if_conditional_ongoing > 0:
                if if_conditional_ongoing == 2:
                    if current_depth == if_conditional_depth:
                        if if_level_not_plus_list[if_level] == 0:
                            if_level += 1
                            function_calls[current_function].append(('start_info','if',if_level,if_new_check))
                            control_flow_list.append(IF_CONTROL)
                        else:
                            if_level_not_plus_list[if_level] = 0

                        if_conditional_ongoing = 0
                else:
                    if_conditional_ongoing = 2  

            if current_has_else == 1:
                has_else_list[if_level + 1] = 1
                current_has_else = 0
           
            #switch-case start
            if switch_depth_list:
                switch_depth = switch_depth_list[-1]
            else:
                switch_depth = -1

            if "SwitchStmt" in line:
                if switch_not_append == 0:
                    switch_case_list.append(0)
                else:
                    switch_not_append = 0
                if switch_new_check >= 1000:
                    switch_new_check = 0
                switch_new_check += 1
                switch_first_ongoing = 1
                temp_switch_current_depth = get_first_alpha_or_angle_index(line)
                switch_ongoing_depth.append(temp_switch_current_depth)
            
            if switch_first_ongoing > 0:
                if switch_first_ongoing == 2:
                    switch_first_ongoing = 0
                    temp_switch_current_depth = get_first_alpha_or_angle_index(line)
                    switch_depth_list.append(temp_switch_current_depth)
                    switch_ongoing = 1
                else:
                    switch_first_ongoing = 2

            if switch_ongoing > 0:
                if switch_ongoing == 2:
                    temp_current_depth = get_first_backtick_index(line)
                    if temp_current_depth == switch_depth:
                        switch_level += 1
                        function_calls[current_function].append(('start_info','switch',switch_level,switch_new_check))
                        control_flow_list.append(SWITCH_CONTROL)
                        switch_ongoing = 0
                else:
                    switch_ongoing = 2
            
            if "CaseStmt" in line or "DefaultStmt" in line:
                if switch_level >= 1:
                    if switch_break == 0 and break_stat == 2:
                        switch_break = 1
                        break_stat = 3      
                    elif switch_break == 0:
                        switch_break = 1
                    if "CaseStmt" in line:
                        function_calls[current_function].append(('start_info','case',switch_level,switch_new_check))
                    if "DefaultStmt" in line:
                        function_calls[current_function].append(('start_info','default',switch_level,switch_new_check))
                    if len(switch_case_list) <= switch_level:
                        switch_case_list.append(0)
                    else:
                        switch_case_list[switch_level] += 1

            if "BreakStmt" in line:             
                break_stat = 1
                function_calls[current_function].append(('end_info','break',break_stat))
                if switch_break == 1 and break_stat != 3:
                    break_stat = 2
                    switch_break = 0
            if "ContinueStmt" in line:
                continue_stat = 1
                function_calls[current_function].append(('end_info','continue',continue_stat))

            if "ReturnStmt" in line:
                temp_return_ongoing_depth = get_first_alpha_or_angle_index(line)
                return_ongoing_depth_list.append(temp_return_ongoing_depth)
                control_flow_list.append(RETURN_CONTROL)

            if "GotoStmt" in line:
                print_error('goto detect',lines,i)
            # Detect the beginning of a function definition (using FunctionDecl)
            if "FunctionDecl" in line:
                match = re.search(r"FunctionDecl\s+[^\']+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*'", line)
                if match:
                    current_function = match.group(1)
                    if current_function in user_functions:
                        function_calls[current_function] = []  # Initialize the call graph for the function
                        if_new_check = 0
                        while_new_check = 0
                        do_while_new_check = 0
                        break_stat = 0
                        continue_stat = 0
                        switch_new_check = 0

            # Handle function call expression
            if "CallExpr" in line and current_function:    
                temp_function_call_depth = get_first_alpha_or_angle_index(line)
                function_call_depth.append(temp_function_call_depth)
                current_call_depth = temp_function_call_depth

            # Detect function calls (CallExpr -> DeclRefExpr) 
            # Includes complete detection of nested function calls
            if "CallExpr" in line and current_function:
                call_check = 0
                for j in range(i + 1, min(i + 20, len(lines))):  # Check a few lines after CallExpr
                    if "DeclRefExpr" in lines[j]:
                        match = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[j])
                        if match:
                            call_check = 1
                            called_function = match.group(1)
                            if called_function in all_functions or called_function in user_functions:  # Track only user-defined functions
                                function_stack.append([
                                    current_call_depth, i, if_level, if_level_else_if_list[if_level],
                                    if_new_check, switch_level, switch_case_list[switch_level],
                                    switch_new_check, while_level, while_new_check,
                                    do_while_level, do_while_new_check,
                                    break_stat, continue_stat, called_function
                                ])
                                break_stat = 0
                                continue_stat = 0

                            if called_function == "clone":
                                for k in range(j + 1, min(j + 20, len(lines))):  # Check a few lines after CallExpr
                                    if "DeclRefExpr" in lines[k]:
                                        match2 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match2:
                                            cloned_function = match2.group(1)
                                            if cloned_function in all_functions or cloned_function in user_functions:
                                                break_stat = 0
                                                continue_stat = 0
                                            break

                            if called_function == "pthread_create":
                                for k in range(j + 1, min(j + 20, len(lines))):  # Check a few lines after CallExpr
                                    if "DeclRefExpr" in lines[k]:
                                        match3 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match3:
                                            pthread_create_function = match3.group(1)
                                            if pthread_create_function in all_functions or pthread_create_function in user_functions:
                                                function_stack.append([
                                                    current_call_depth, i, if_level, if_level_else_if_list[if_level],
                                                    if_new_check, switch_level, switch_case_list[switch_level],
                                                    switch_new_check, while_level, while_new_check,
                                                    do_while_level, do_while_new_check,
                                                    break_stat, continue_stat, pthread_create_function
                                                ])
                                                break_stat = 0
                                                continue_stat = 0
                                            break
                        break
         
        while function_stack and current_function:
            function_calls[current_function].append(function_stack.pop())
            if function_stack:
                if function_stack[-1][11] == 'clone' or function_stack[-1][11] == 'pthread_create':
                    temp_function_stack = function_calls[current_function].pop()
                    function_calls[current_function].append(function_stack.pop())
                    function_calls[current_function].append(temp_function_stack)
            else:
                break
                
        return function_calls,user_functions,all_functions
    except Exception as e:
        print(f"Error extracting function call graph: {e}")
        print(e)
        return {}
    
def control_flow_check(callee):
    control_return_val = 0
    if 'end_info' != callee[0] and 'start_info' != callee[0]:
        return NORMAL_CONTROL
    if callee[1] == 'if' or callee[1] == 'conditional' or callee[1] == 'else if' or callee[1] == 'else':
        control_return_val |= IF_CONTROL
    if callee[1] == 'switch' or callee[1] == 'case' or callee[1] =='default':
        control_return_val |= SWITCH_CONTROL
    if callee[1] == 'for' or callee[1] =='while' or callee[1] == 'while conditional' or callee[1] == 'for conditional first' or callee[1] == 'for conditional second':
        control_return_val |= WHILE_CONTROL
    if callee[1] == 'do_while' or callee[1] == 'do_while conditional':
        control_return_val |= DO_WHILE_CONTROL
    if callee[1] == 'break':
        control_return_val |= BREAK_CONTROL
    if callee[1] == 'continue':
        control_return_val |= CONTINUE_CONTROL
    if callee[1] == 'return':
        control_return_val |= RETURN_CONTROL
    if control_return_val == NORMAL_CONTROL:
        print_error_for_make_function('control flow error')
    return control_return_val

def make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee):
    if not prev_callee_list:
        print_error_for_make_function('make connect error')
    for temp_prev_callee in prev_callee_list:
        call_graph_matrix[caller][temp_prev_callee[-1]][call_graph_function_pos[caller][callee[-1]]] = 1

def check_and_list_append(dst_list,input):
    if function_not_in_list(input,dst_list) == 0:
        dst_list.append(input)

def print_matrix(call_graph_matrix,call_graph_function_pos,caller):
    for function_name, function_call_matrix in call_graph_matrix[caller].items():
        temp_connect_list = []
        for temp_index in range(0,len(function_call_matrix)):
            temp_function_pos = {value: key for key, value in call_graph_function_pos[caller].items()}
            if function_call_matrix[temp_index] == 1:
                temp_connect_list.append(temp_function_pos[temp_index])
        if 'iteration lambda' not in function_name:
            print(function_name,temp_connect_list)

def function_not_in_list_use_function_name(callee,function_name_list):
    check = 0
    for function_name in function_name_list:
        if callee[-1] == function_name:
            check = 1
            break
    return check

def function_not_in_list(callee,callee_list):
    check = 0
    for temp_callee in callee_list:
        if callee[-1] == temp_callee[-1]:
            check = 1
            break
    return check

def control_flow_skip_check(prev_control_flow_skip,control_flow_return_val, callee,control_flow_skip_list):
    return_val = prev_control_flow_skip
    if control_flow_skip_list:
        if control_flow_skip_list[-1][0] == control_flow_return_val:
            if control_flow_return_val == WHILE_CONTROL:
                if callee[2] == control_flow_skip_list[-1][1][2]:
                    control_flow_skip_list.pop()
                    return_val = 0
            elif control_flow_return_val == DO_WHILE_CONTROL:
                if control_flow_skip_list[-1][2] == 'break':     # break
                    if callee[0] == 'end_info' and callee[2] == control_flow_skip_list[-1][1][2]:
                        control_flow_skip_list.pop()
                        return_val = 0
                else:                                            # continue
                    if callee[0] == 'start_info' and callee[2] == control_flow_skip_list[-1][1][2]:
                        control_flow_skip_list.pop()
                        return_val = 0
            elif control_flow_return_val == SWITCH_CONTROL:
                if callee[2] == control_flow_skip_list[-1][1][2]:
                    return_val = 0
        elif control_flow_skip_list[-1][0] == RETURN_CONTROL:
            if control_flow_skip_list[-1][1] == 1:
                if control_flow_skip_list[-1][2] == control_flow_return_val:
                    if callee[2] == control_flow_skip_list[-1][3]:
                        control_flow_skip_list.pop()
                        return_val = 0
    return return_val
def make_matrix_from_function_graph(function_graph,function_not_call):
    call_graph_matrix = {}
    call_graph_function_pos = {}
    call_graph_function_start  = {}
    call_graph_function_end = {}
    call_graph_matrix_list = []
    call_graph_function_pos_list = []
    caller_list = []
    not_call_function_set = set([])
    prev_not_call_function_set = set([])
    while_check = 0
    all_combinations = [set()]
    all_combinations_index = 0
    function_name_dict = {}
    reverse_call_graph_function_pos = {}
    reverse_call_graph_function_pos_per_caller = {}
    control_flow_iteration_lambda_function = []
    while True:
        if while_check == 0:
            call_graph_matrix = {}
            call_graph_function_pos = {}
            call_graph_function_start  = {}
            call_graph_function_end = {}
            call_graph_matrix_list = []
            call_graph_function_pos_list = []
            caller_list = []
        for caller, callees in function_graph.items():
            if caller in function_not_call:
                continue
            if while_check == 0 or function_name_dict[caller] == 0:
                call_graph_matrix[caller] = {}
                call_graph_function_pos[caller] = {}
                call_graph_function_start[caller] = []
                call_graph_function_end[caller] = []
                function_name_dict[caller] = 0
            if callees:
                if while_check == 0 or function_name_dict[caller] == 0:
                    control_flow_iteration_lambda_function = []
                    function_list = []
                    for i in range(0,MAX_SINGLE_CONTROL_FLOW_COUNT):
                        function_list.append('iteration lambda function' + str(i))
                        control_flow_iteration_lambda_function.append('iteration lambda function' + str(i))

                    function_list.append('S')
                    function_list.append('E')
                    for callee in callees:
                        if control_flow_check(callee) == NORMAL_CONTROL:
                            if callee[-1] not in function_not_call:
                                if function_not_in_list_use_function_name(callee,function_list) == 0:
                                    function_list.append(callee[-1])
                    index = 0
                    if FOR_DEVELOPMENT == 1:
                        print(caller)
                    for function_name in function_list:
                        if function_name not in function_not_call:
                            call_graph_matrix[caller][function_name] = [0]*len(function_list)
                            call_graph_function_pos[caller][function_name] = index
                            index += 1
                    # Completed basic matrix construction and index mapping for each function
                    reverse_call_graph_function_pos = {value: key for key, value in call_graph_function_pos[caller].items()}
                    reverse_call_graph_function_pos_per_caller[caller] = reverse_call_graph_function_pos

                    if while_check == 1:
                        function_name_dict[caller] = 1
                reverse_call_graph_function_pos = reverse_call_graph_function_pos_per_caller[caller]
                if while_check == 1:
                    not_call_function_set = all_combinations[all_combinations_index]

                end_callee = (0,0,0,0,0,0,0,0,0,0,0,0,0,0,'E')
                if end_callee not in callees:
                    callees.append(end_callee)

                prev_callee = (0,0,0,0,0,0,0,0,0,0,0,0,0,0,'S')
                prev_callee_list = []
                prev_callee_list.append(prev_callee)
            
                control_flow_iteration_lambda_function_pos = -1
                control_flow_list = []  # List to store control flow information
                control_flow = 0
                control_flow_end = 0
                control_flow_can_empty = 1  # 0 = cannot be empty, 1 = may be empty
                control_flow_can_empty_list = []
                control_flow_end_list = []
                control_flow_level = 0
                control_flow_information_list = []  # List to store the starting info of each control flow block
                control_flow_skip = 0
                control_flow_skip_list = []

                if_prev_start_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]  # Stack for functions just before if/ternary conditional blocks
                if_ongoing_start_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]  # Stack for functions inside else-if blocks
                if_ongoing_end_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]
                if_working_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT  # Marks whether any function call occurred inside the if block
                if_function_in_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT  # Tracks if any function exists in the if block
                if_function_in_final_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT  # Marked as 1 if no function exists in the if block
                else_if_ongoing_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                else_ongoing_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                if_ongoing_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                if_return_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]
                if_return_cul_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]

                switch_prev_start_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]  # Stack for functions just before switch-case blocks
                switch_ongoing_start_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]  # Stack for functions inside switch-case blocks
                switch_ongoing_end_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]
                switch_working_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT  # Marks whether any function call occurred inside the switch block
                switch_function_in_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                switch_function_in_final_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                switch_ongoing_start_level_list = []
                switch_prev_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                case_ongoing_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                default_ongoing_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                switch_ongoing_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                switch_break_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]
                switch_ongoing_break_if = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                switch_ongoing_break = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                switch_return_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]
                switch_return_cul_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]

                while_prev_start_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]  # Stack for functions just before while blocks
                while_ongoing_start_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]  # Stack for functions inside while blocks
                while_ongoing_end_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]
                while_ongoing_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                for_ongoing_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                while_working_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT  # Used to detect function calls inside loop body due to 'continue'

                while_conditional_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]  # Functions inside while loop conditions
                while_conditional_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT

                iteration_break_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]
                iteration_continue_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]
                iteration_ongoing_break = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT

                for_first_conditional_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                for_second_conditional_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT

                for_first_conditional_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]  # Functions in the first clause of a for-loop
                for_second_conditional_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]  # Functions in the second clause of a for-loop

                for_prev_start_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]
                for_ongoing_start_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]
                for_ongoing_end_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)]

                do_while_ongoing_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                do_while_working_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                do_while_ongoing_start_list =  [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)] 
                do_while_conditional_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)] 
                do_while_conditional_start = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                do_while_prev_start_list =  [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)] 
                do_while_ongoing_end_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)] 
                do_while_function_call_normal_list = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT
                do_while_break_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)] 
                do_while_continue_list = [[] for _ in range(MAX_SINGLE_CONTROL_FLOW_COUNT)] 
                do_while_ongoing_break = [0] * MAX_SINGLE_CONTROL_FLOW_COUNT


                copy_callees = callees.copy()

                callee_index = 0
                while callee_index < len(copy_callees):
                    callee = copy_callees[callee_index]
                    callee_index += 1

                    control_flow_return_val = control_flow_check(callee)
                    if control_flow_return_val != NORMAL_CONTROL:
                        control_flow_skip = control_flow_skip_check(control_flow_skip,control_flow_return_val,callee,control_flow_skip_list)
                    else:
                        if callee[-1] == 'E':
                            control_flow_skip = 0
                    if control_flow_skip == 1:
                        if FOR_DEVELOPMENT == 1:
                            print('for Debug','skip',callee)
                        continue
                    else:
                        if FOR_DEVELOPMENT == 1:
                            print('for Debug',callee)
                    if control_flow_return_val == NORMAL_CONTROL:
                        temp_for_second = 0
                        if callee[-1] in function_not_call:     # Skip functions that do not call any other function at all
                            continue
                        if callee[-1] in not_call_function_set: # Also skip functions that may possibly not call any function
                            continue
                        if not prev_callee_list:
                            print_error_for_make_function('prev_callee_list error1')
                        if control_flow_list:
                            control_flow = control_flow_list[-1]
                            if control_flow == IF_CONTROL:
                                if if_ongoing_start[callee[2]] == 1:
                                    if_working_list[callee[2]] = 1
                                    if_function_in_list[callee[2]] = 1  # There was at least one function in 'if'
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                    if else_if_ongoing_start[callee[2]] == 1:                 
                                        else_if_ongoing_start[callee[2]] = 2
                                        if_ongoing_start_list[callee[2]][-1].append(callee)
                                        if_ongoing_end_list[callee[2]][-1].append(callee)
                                        make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                    elif else_if_ongoing_start[callee[2]] == 2:
                                        if if_ongoing_end_list[callee[2]]:
                                            if_ongoing_end_list[callee[2]][-1].clear()
                                            if_ongoing_end_list[callee[2]][-1].append(callee)
                                        else:
                                            print_error_for_make_function('if_ongoing_end_list')
                                        make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                            elif control_flow == SWITCH_CONTROL:
                                if switch_ongoing_start[callee[5]] == 1:
                                    switch_working_list[callee[5]] = 1
                                    switch_function_in_list[callee[5]] = 1  # There was at least one function in 'switch'
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                    if case_ongoing_start[callee[5]] == 1:
                                        case_ongoing_start[callee[5]] = 2
                                        switch_ongoing_end_list[callee[5]][-1].append(callee)
                                        make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                    elif case_ongoing_start[callee[5]] == 2:
                                        if switch_ongoing_end_list[callee[5]]:
                                            switch_ongoing_end_list[callee[5]][-1].clear()
                                            switch_ongoing_end_list[callee[5]][-1].append(callee)
                                        else:
                                            print_error_for_make_function('switch_ongoing_end_list')
                                        make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                            elif control_flow == WHILE_CONTROL:
                                while_working_list[callee[8]] = 1  
                                if control_flow_end == 1:
                                    control_flow_end = 0                               
                                if while_conditional_start[callee[8]] == 1: # If a function call occurs inside the while loop condition
                                    while_conditional_list[callee[8]].append(callee)
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                elif for_first_conditional_start[callee[8]] == 1:  # If a function call occurs during the first condition check of a for loop
                                    for_first_conditional_list[callee[8]].append(callee)
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                elif for_second_conditional_start[callee[8]] == 1:  # If a function call occurs during the second clause of a for loop (increment step)
                                    for_second_conditional_list[callee[8]].append(callee)
                                    temp_for_second = 1
                                    # The second clause of a for-loop is not executed immediately,
                                    # so it should not be connected here
                                elif while_ongoing_start[callee[8]] == 1:   # If a function call occurs inside the body of a loop (not in the condition)
                                    if len(while_ongoing_start_list[callee[8]]) < 2:   # If no function was called previously in the while condition or body
                                        while_ongoing_start_list[callee[8]].append(callee)
                                    if not while_ongoing_end_list[callee[8]]:
                                        while_ongoing_end_list[callee[8]].append(callee)
                                    else:
                                        while_ongoing_end_list[callee[8]].clear()
                                        while_ongoing_end_list[callee[8]].append(callee)
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                elif for_ongoing_start[callee[8]] == 1:
                                    if len(for_ongoing_start_list[callee[8]]) < 2:  # Need to confirm that there's more than just the lambda
                                        for_ongoing_start_list[callee[8]].append(callee)
                                    if not for_ongoing_end_list[callee[8]]:
                                        for_ongoing_end_list[callee[8]].append(callee)
                                    else:
                                        for_ongoing_end_list[callee[8]].clear()
                                        for_ongoing_end_list[callee[8]].append(callee)
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                else:
                                    print_error_for_make_function('while_control_error')    
                            elif control_flow == DO_WHILE_CONTROL:
                                do_while_working_list[callee[10]] = 1
                                do_while_function_call_normal_list[callee[10]] = 1
                                if control_flow_end == 1:
                                    control_flow_end = 0
                                if do_while_ongoing_start[callee[10]] == 1:
                                    if len(do_while_ongoing_start_list[callee[10]]) < 2: # Store up to 2 items, accounting for the number of lambdas
                                        do_while_ongoing_start_list[callee[10]].append(callee)
                                    if not do_while_ongoing_end_list[callee[10]]:
                                        do_while_ongoing_end_list[callee[10]].append(callee)
                                    else:
                                        do_while_ongoing_end_list[callee[10]].clear()
                                        do_while_ongoing_end_list[callee[10]].append(callee)
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                elif do_while_conditional_start[callee[10]] == 1:
                                    if not do_while_conditional_list[callee[10]]:
                                        do_while_conditional_list[callee[10]].append(callee)
                                    if not do_while_ongoing_end_list[callee[10]]:
                                        do_while_ongoing_end_list[callee[10]].append(callee)
                                    else:
                                        do_while_ongoing_end_list[callee[10]].clear()
                                        do_while_ongoing_end_list[callee[10]].append(callee)
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                else:
                                    print_error_for_make_function('do_while_control_error')             
                        else:
                            make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                        if temp_for_second == 0:    # In the second part of a for-loop, previous function info must not be updated
                            prev_callee_list.clear()
                            prev_callee_list.append(callee)
                    else:
                        if control_flow_return_val == IF_CONTROL:
                            if callee[0] == 'start_info':
                                if callee[1] == 'if' or callee[1] == 'conditional':
                                    control_flow_can_empty_list.append(control_flow_can_empty)
                                    control_flow_can_empty = 1
                                    control_flow_end_list.append(control_flow_end)
                                    control_flow_end = 0
                                    control_flow_list.append(control_flow_return_val)
                                    control_flow_information_list.append(callee)
                                    control_flow_level += 1
                                    else_if_ongoing_start[callee[2]] = 1
                                    if_ongoing_start[callee[2]] = 1
                                    for temp_prev_callee in prev_callee_list:
                                        if_prev_start_list[callee[2]].append(temp_prev_callee)
                                    prev_callee_list.clear()
                                    for temp_if_prev_start in if_prev_start_list[callee[2]]:
                                        prev_callee_list.append(temp_if_prev_start)
                                    if_ongoing_start_list[callee[2]].append([])
                                    if_ongoing_end_list[callee[2]].append([])
                                elif callee[1] == 'else if':
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                        if control_flow_can_empty == 0:         # Indicates that the previous block cannot be empty
                                            if_function_in_list[callee[2]] = 1
                                        if not if_ongoing_start_list[callee[2]][-1]:    # If nothing was called previously (the list is empty)
                                            for temp_prev_callee in prev_callee_list:
                                                if temp_prev_callee[4] >= control_flow_information_list[-1][3] and temp_prev_callee[4] <= callee[3]: 
                                                    if_working_list[callee[2]] = 1
                                                    if_ongoing_start_list[callee[2]][-1].append(temp_prev_callee)
                                        if_ongoing_end_list[callee[2]][-1].clear()
                                        for temp_prev_callee in prev_callee_list:
                                            if temp_prev_callee[4] >= control_flow_information_list[-1][3] and temp_prev_callee[4] <= callee[3]: 
                                                if_working_list[callee[2]] = 1
                                                if_ongoing_end_list[callee[2]][-1].append(temp_prev_callee)  


                                    if if_return_list[callee[2]]:
                                        for temp_if_return_list in if_return_list[callee[2]]:
                                            make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_if_return_list,end_callee)
                                        if_return_list[callee[2]].clear()
                                        if if_ongoing_end_list[callee[2]]:
                                            if_ongoing_end_list[callee[2]].pop()
                                        if_function_in_list[callee[2]] = 1

                                    if if_function_in_list[callee[2]] == 0:
                                        if_function_in_final_list[callee[2]] = 1
                                    if_function_in_list[callee[2]] = 0
                                    else_if_ongoing_start[callee[2]] = 1


                                                        
                                    prev_callee_list.clear()
                                    for temp_if_prev_start in if_prev_start_list[callee[2]]:
                                        prev_callee_list.append(temp_if_prev_start)
                                    if_ongoing_end_list[callee[2]].append([])
                                    if_ongoing_start_list[callee[2]].append([])
                                elif callee[1] == 'else':
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                        if control_flow_can_empty == 0:
                                            if_function_in_list[callee[2]] = 1
                                        if not if_ongoing_start_list[callee[2]][-1]:
                                            for temp_prev_callee in prev_callee_list:
                                                if temp_prev_callee[4] >= control_flow_information_list[-1][3] and temp_prev_callee[4] <= callee[3]: 
                                                    if_working_list[callee[2]] = 1
                                                    if_ongoing_start_list[callee[2]][-1].append(temp_prev_callee)
                                        if_ongoing_end_list[callee[2]][-1].clear()
                                        for temp_prev_callee in prev_callee_list:
                                            if temp_prev_callee[4] >= control_flow_information_list[-1][3] and temp_prev_callee[4] <= callee[3]: 
                                                if_working_list[callee[2]] = 1
                                                if_ongoing_end_list[callee[2]][-1].append(temp_prev_callee)

                                    if if_return_list[callee[2]]:
                                        for temp_if_return_list in if_return_list[callee[2]]:
                                            make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_if_return_list,end_callee)
                                        if_return_list[callee[2]].clear()
                                        if if_ongoing_end_list[callee[2]]:
                                            if_ongoing_end_list[callee[2]].pop()    # Since [] is always appended, pop() must be called whenever a return is present
                                        if_function_in_list[callee[2]] = 1

                                    if if_function_in_list[callee[2]] == 0:
                                        if_function_in_final_list[callee[2]] = 1
                                    if_function_in_list[callee[2]] = 0
                                    else_if_ongoing_start[callee[2]] = 1
                                    else_ongoing_start[callee[2]] = 1

                                    if_ongoing_end_list[callee[2]].append([])
                                    if_ongoing_start_list[callee[2]].append([])
                                    prev_callee_list.clear()
                                    for temp_if_prev_start in if_prev_start_list[callee[2]]:
                                        prev_callee_list.append(temp_if_prev_start)
                                else:
                                    print_error_for_make_function('IF_CONTROL error1')
                            else:   
                                if control_flow_end == 1:
                                    control_flow_end = 0
                                    if control_flow_can_empty == 0:         # Indicates that the previous block cannot be empty
                                        if_function_in_list[callee[2]] = 1
                                    # Even though no function was called directly in the 'else' block,
                                    # a control flow occurred and a function was called within it
                                    if not if_ongoing_start_list[callee[2]][-1]:   
                                        for temp_prev_callee in prev_callee_list:
                                            if temp_prev_callee[4] >= control_flow_information_list[-1][3] and temp_prev_callee[4] <= callee[3]: 
                                                if_working_list[callee[2]] = 1
                                                if_ongoing_start_list[callee[2]][-1].append(temp_prev_callee)
                                    if_ongoing_end_list[callee[2]][-1].clear()
                                    for temp_prev_callee in prev_callee_list:
                                        if temp_prev_callee[4] >= control_flow_information_list[-1][3] and temp_prev_callee[4] <= callee[3]: 
                                            if_working_list[callee[2]] = 1
                                            if_ongoing_end_list[callee[2]][-1].append(temp_prev_callee)

                                if if_return_list[callee[2]]:
                                    for temp_if_return_list in if_return_list[callee[2]]:
                                        make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_if_return_list,end_callee)
                                    if_return_list[callee[2]].clear()
                                    if if_ongoing_end_list[callee[2]]:
                                        if_ongoing_end_list[callee[2]].pop()
                                    if_function_in_list[callee[2]] = 1 # Treat return as equivalent to a function call to prevent prev_if from being linked after the if-block


                                temp_control_flow_can_empty_check = 0
                                if else_ongoing_start[callee[2]] == 0:
                                    temp_control_flow_can_empty_check = 1
                                else:
                                    if if_function_in_list[callee[2]] != 1 or if_function_in_final_list[callee[2]] != 0:
                                        temp_control_flow_can_empty_check = 1
                                if temp_control_flow_can_empty_check == 0:
                                    control_flow_can_empty = 0

                                
                                prev_callee_list.clear()
                                if if_ongoing_end_list[callee[2]]:
                                    for temp_if_ongoing_end_list in if_ongoing_end_list[callee[2]]:
                                        for temp_prev_callee in temp_if_ongoing_end_list:
                                            if function_not_in_list(temp_prev_callee,prev_callee_list) == 0:
                                                prev_callee_list.append(temp_prev_callee)
                                    
                                if else_ongoing_start[callee[2]] == 0:
                                    if if_prev_start_list[callee[2]]:
                                        for temp_if_prev_start in if_prev_start_list[callee[2]]:
                                            if function_not_in_list(temp_if_prev_start,prev_callee_list) == 0:
                                                prev_callee_list.append(temp_if_prev_start)
                                else:
                                    if if_function_in_list[callee[2]] != 1 or if_function_in_final_list[callee[2]] != 0:
                                        if if_prev_start_list[callee[2]]:
                                            for temp_if_prev_start in if_prev_start_list[callee[2]]:
                                                if function_not_in_list(temp_if_prev_start,prev_callee_list) == 0:
                                                    prev_callee_list.append(temp_if_prev_start)
                                    else:       
                                        # 'else' exists and all if, else-if, and else blocks contained function calls,
                                        # yet the end list is empty — this implies that every branch ended with a return
                                        if not if_ongoing_end_list[callee[2]]:
                                            for temp_if_return_cul_list in if_return_cul_list[callee[2]]:
                                                for temp_if_return_cul in temp_if_return_cul_list:
                                                        if function_not_in_list(temp_if_return_cul,prev_callee_list) == 0:
                                                            prev_callee_list.append(temp_if_return_cul)
                                            copy_callees.insert(callee_index,('end_info','return',1,'temp return'))
                                
                                if_ongoing_start[callee[2]] = 0
                                else_if_ongoing_start[callee[2]] = 0
                                if_function_in_list[callee[2]] = 0
                                if_function_in_final_list[callee[2]] = 0
                                else_ongoing_start[callee[2]] = 0
                                if_prev_start_list[callee[2]].clear()
                                if_ongoing_start_list[callee[2]].clear()
                                if_ongoing_end_list[callee[2]].clear()
                                if_return_list[callee[2]].clear()
                                if_return_cul_list[callee[2]].clear()
                                if control_flow_can_empty == 1:
                                    control_flow_can_empty = control_flow_can_empty_list[-1]
                                control_flow_can_empty_list.pop()
                                if control_flow_list:
                                    if if_working_list[callee[2]] == 1:
                                        control_flow_end = 1
                                    else:
                                        control_flow_end = control_flow_end_list[-1]
                                    if control_flow_list.pop() != IF_CONTROL:
                                        print('control flow is not if control!!')
                                    control_flow_information_list.pop()
                                else:
                                    print_error_for_make_function('control flow empty!!')
                                if_working_list[callee[2]] = 0

                        elif control_flow_return_val == SWITCH_CONTROL:
                            if callee[0] == 'start_info':
                                if callee[1] == 'switch':
                                    control_flow_can_empty_list.append(control_flow_can_empty)
                                    control_flow_can_empty = 1
                                    control_flow_end_list.append(control_flow_end)
                                    control_flow_end = 0
                                    control_flow_list.append(control_flow_return_val)
                                    control_flow_information_list.append(callee)
                                    control_flow_level += 1
                                    switch_prev_start[callee[2]] = 1
                                    case_ongoing_start[callee[2]] = 1
                                    switch_ongoing_start[callee[2]] = 1
                                    for temp_prev_callee in prev_callee_list:
                                        switch_prev_start_list[callee[2]].append(temp_prev_callee)
                                    switch_ongoing_start_level_list.append(callee[2])
                                    prev_callee_list.clear()
                                    for temp_switch_prev_start in switch_prev_start_list[callee[2]]:
                                        prev_callee_list.append(temp_switch_prev_start)
                                    switch_function_in_list[callee[2]] = 1 # There may be no function calls between the first switch statement and the next case
                                    switch_ongoing_break_if[callee[2]] = 1
                                elif callee[1] == 'case':
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                        if control_flow_can_empty == 0:
                                            switch_function_in_list[callee[2]] = 1
                                        if switch_ongoing_end_list[callee[2]]:
                                            switch_ongoing_end_list[callee[2]][-1].clear()
                                            for temp_prev_callee in prev_callee_list:
                                                if temp_prev_callee[7] >= control_flow_information_list[-1][3] and temp_prev_callee[7] <= callee[3]:
                                                    switch_working_list[callee[2]] = 1
                                                    switch_ongoing_end_list[callee[2]][-1].append(temp_prev_callee)
                                        else:
                                            print_error_for_make_function('control_flow in switch - case?')

                                    if switch_return_list[callee[2]] and switch_ongoing_break_if[callee[2]] == 1:
                                        print_error_for_make_function('break, return in case?')

                                    # 'return' related processing
                                    if switch_return_list[callee[2]]:
                                        for temp_switch_return_list in switch_return_list[callee[2]]:
                                            make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_switch_return_list,end_callee)
                                        switch_return_list[callee[2]].clear()
                                        if switch_function_in_list[callee[2]] == 1:
                                            if switch_ongoing_end_list[callee[2]]:
                                                switch_ongoing_end_list[callee[2]].pop()
                                        case_ongoing_start[callee[2]] = 1
                                        switch_ongoing_end_list[callee[2]].append([])

                                    
                                    if switch_ongoing_break_if[callee[2]] == 1:
                                        if switch_function_in_list[callee[2]] == 0:
                                            switch_function_in_final_list[callee[2]] = 1
                                        case_ongoing_start[callee[2]] = 1
                                    switch_function_in_list[callee[2]] = 0

                                    prev_callee_list.clear()
                                    for temp_switch_prev_start in switch_prev_start_list[callee[2]]:
                                        prev_callee_list.append(temp_switch_prev_start)
                                    if switch_ongoing_break[callee[2]] == 0:
                                        if switch_ongoing_end_list[callee[2]]:
                                            if switch_ongoing_end_list[callee[2]][-1]:
                                                for temp_switch_ongoing_end in switch_ongoing_end_list[callee[2]][-1]:
                                                    if function_not_in_list(temp_switch_ongoing_end,prev_callee_list) == 0:
                                                        prev_callee_list.append(temp_switch_ongoing_end)
                                    if switch_ongoing_break_if[callee[2]] == 1:
                                        # Add an empty list only when a 'break' is present;
                                        # if not, the existing list will continue to be used
                                        switch_ongoing_end_list[callee[2]].append([])
                                    switch_ongoing_break_if[callee[2]] = 0
                                    switch_ongoing_break[callee[2]] = 0
                                elif callee[1] == 'default':
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                        if control_flow_can_empty == 0:
                                            switch_function_in_list[callee[2]] = 1
                                        if switch_ongoing_end_list[callee[2]]:
                                            switch_ongoing_end_list[callee[2]][-1].clear()
                                            for temp_prev_callee in prev_callee_list:
                                                if temp_prev_callee[7] >= control_flow_information_list[-1][3] and temp_prev_callee[7] <= callee[3]:
                                                    switch_working_list[callee[2]] = 1
                                                    switch_ongoing_end_list[callee[2]][-1].append(temp_prev_callee)
                                        else:
                                            print_error_for_make_function('control_flow in switch - case?')

                                    # 'return' related processing
                                    if switch_return_list[callee[2]]:
                                        for temp_switch_return_list in switch_return_list[callee[2]]:
                                            make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_switch_return_list,end_callee)
                                        switch_return_list[callee[2]].clear()
                                        if switch_function_in_list[callee[2]] == 1:
                                            if switch_ongoing_end_list[callee[2]]:
                                                switch_ongoing_end_list[callee[2]].pop()
                                        case_ongoing_start[callee[2]] = 1
                                        switch_ongoing_end_list[callee[2]].append([])

                                    if switch_ongoing_break_if[callee[2]] == 1:
                                        if switch_function_in_list[callee[2]] == 0:
                                            switch_function_in_final_list[callee[2]] = 1
                                        case_ongoing_start[callee[2]] = 1
                                    switch_function_in_list[callee[2]] = 0
                                    default_ongoing_start[callee[2]] = 1
                                    prev_callee_list.clear()
                                    for temp_switch_prev_start in switch_prev_start_list[callee[2]]:
                                        prev_callee_list.append(temp_switch_prev_start)

                                    if switch_ongoing_break[callee[2]] == 0:
                                        for temp_index in range(len(switch_ongoing_end_list[callee[2]]) - 1, -1, -1):
                                            if switch_ongoing_end_list[callee[2]][temp_index]:
                                                for temp_switch_ongoing_end in switch_ongoing_end_list[callee[2]][temp_index]:
                                                    if function_not_in_list(temp_switch_ongoing_end,prev_callee_list) == 0:
                                                        prev_callee_list.append(temp_switch_ongoing_end)
                                                break

                                    if switch_ongoing_break_if[callee[2]] == 1:
                                        switch_ongoing_end_list[callee[2]].append([])
                                    switch_ongoing_break_if[callee[2]] = 0
                                    switch_ongoing_break[callee[2]] = 0

                                else:
                                    print_error_for_make_function('switch control error1')
                            else:
                                if control_flow_end == 1:
                                    control_flow_end = 0
                                    if control_flow_can_empty == 0:
                                        switch_function_in_list[callee[2]] = 1
                                    switch_ongoing_end_list[callee[2]][-1].clear()
                                    for temp_prev_callee in prev_callee_list:
                                        if temp_prev_callee[7] >= control_flow_information_list[-1][3] and temp_prev_callee[7] <= callee[3]:
                                            switch_working_list[callee[2]] = 1
                                            switch_ongoing_end_list[callee[2]][-1].append(temp_prev_callee)

                                # 'return' related processing
                                if switch_return_list[callee[2]]:
                                    for temp_switch_return_list in switch_return_list[callee[2]]:
                                        make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_switch_return_list,end_callee)
                                    switch_return_list[callee[2]].clear()
                                    if switch_function_in_list[callee[2]] == 1:
                                        if switch_ongoing_end_list[callee[2]]:
                                            switch_ongoing_end_list[callee[2]].pop()
                                    case_ongoing_start[callee[2]] = 1
                                    switch_ongoing_end_list[callee[2]].append([])

                                temp_control_flow_can_empty_check = 0
                                if default_ongoing_start[callee[2]] == 0:
                                    temp_control_flow_can_empty_check = 1
                                else:
                                    if switch_function_in_list[callee[2]] != 1 or switch_function_in_final_list[callee[2]] != 0:
                                        temp_control_flow_can_empty_check = 1
                                if temp_control_flow_can_empty_check == 0:
                                    control_flow_can_empty = 0     

                                prev_callee_list.clear()
                                if switch_ongoing_end_list[callee[2]]:
                                    for temp_switch_ongoing_end_list in switch_ongoing_end_list[callee[2]]:
                                        for temp_prev_callee in temp_switch_ongoing_end_list:
                                            if function_not_in_list(temp_prev_callee,prev_callee_list) == 0:
                                                prev_callee_list.append(temp_prev_callee)
                                if default_ongoing_start[callee[2]] == 0:
                                    if switch_prev_start_list[callee[2]]:
                                        for temp_switch_prev_start in switch_prev_start_list[callee[2]]:
                                            if function_not_in_list(temp_switch_prev_start,prev_callee_list) == 0:
                                                prev_callee_list.append(temp_switch_prev_start)
                                else:
                                    if switch_function_in_list[callee[2]] != 1 or switch_function_in_final_list[callee[2]] != 0:
                                        if switch_prev_start_list[callee[2]]:
                                            for temp_switch_prev_start in switch_prev_start_list[callee[2]]:
                                                if function_not_in_list(temp_switch_prev_start,prev_callee_list) == 0:
                                                    prev_callee_list.append(temp_switch_prev_start)
                                    else:
                                        if not switch_ongoing_end_list[callee[2]]:
                                            for temp_switch_return_cul_list in switch_return_cul_list[callee[2]]:
                                                for temp_switch_return_cul in temp_switch_return_cul_list:
                                                        if function_not_in_list(temp_switch_return_cul,prev_callee_list) == 0:
                                                            prev_callee_list.append(temp_switch_return_cul)
                                            copy_callees.insert(callee_index,('end_info','return',1,'temp return'))                                     

                                if switch_break_list[callee[2]]:
                                    for temp_switch_break_list in switch_break_list[callee[2]]:
                                        for temp_switch_break in temp_switch_break_list:
                                            if function_not_in_list(temp_switch_break,prev_callee_list) == 0:
                                                prev_callee_list.append(temp_switch_break)
                            
                                switch_return_list[callee[2]].clear()
                                switch_return_cul_list[callee[2]].clear()                            
                                switch_prev_start[callee[2]] = 0
                                switch_ongoing_start[callee[2]] = 0
                                case_ongoing_start[callee[2]] = 0
                                switch_ongoing_end_list[callee[2]].clear()
                                switch_function_in_list[callee[2]] = 0
                                switch_function_in_final_list[callee[2]] = 0
                                default_ongoing_start[callee[2]] = 0
                                switch_prev_start_list[callee[2]].clear()
                                switch_ongoing_start_list[callee[2]].clear()
                                switch_ongoing_start_level_list.pop()
                                switch_break_list[callee[2]].clear()
                                switch_ongoing_break_if[callee[2]] = 0
                                switch_ongoing_break[callee[2]] = 0
                                if control_flow_can_empty == 1:
                                    control_flow_can_empty = control_flow_can_empty_list[-1]
                                control_flow_can_empty_list.pop()
                                if control_flow_list:
                                    if switch_working_list[callee[2]] == 1:
                                        control_flow_end = 1
                                    else:
                                        control_flow_end = control_flow_end_list[-1]
                                    control_flow_end_list.pop()
                                    if control_flow_list.pop() != SWITCH_CONTROL:
                                        print('control flow is not switch control!!')
                                    control_flow_information_list.pop()
                                else:
                                    print_error_for_make_function('control flow empty!!')
                                switch_working_list[callee[2]] = 0
                        
                        elif control_flow_return_val == WHILE_CONTROL:
                            if callee[0] == 'start_info':
                                if callee[1] == 'while conditional':
                                    control_flow_can_empty_list.append(control_flow_can_empty)
                                    control_flow_can_empty = 1
                                    control_flow_iteration_lambda_function_pos += 1
                                    control_flow_list.append(control_flow_return_val)
                                    control_flow_information_list.append(callee)
                                    control_flow_level += 1
                                    control_flow_end_list.append(control_flow_end)  # Store the previous control_flow_end state
                                    control_flow_end = 0    # Reset to 0, since the previous control_flow_end did not occur inside the current block
                                    while_conditional_start[callee[2]] = 1
                                    for temp_prev_callee in prev_callee_list:
                                        while_prev_start_list[callee[2]].append(temp_prev_callee)
                                elif callee[1] == 'for conditional first':
                                    control_flow_can_empty_list.append(control_flow_can_empty)
                                    control_flow_can_empty = 1
                                    control_flow_end_list.append(control_flow_end)
                                    control_flow_end = 0
                                    control_flow_iteration_lambda_function_pos += 1
                                    control_flow_list.append(control_flow_return_val)
                                    control_flow_information_list.append(callee)
                                    control_flow_level += 1
                                    for_first_conditional_start[callee[2]] = 1
                                    for temp_prev_callee in prev_callee_list:
                                        for_prev_start_list[callee[2]].append(temp_prev_callee)
                                elif callee[1] == 'for conditional second':
                                    for_second_conditional_start[callee[2]] = 1
                                elif callee[1] == 'for':
                                    for_ongoing_start[callee[2]] = 1
                                    if for_first_conditional_list[callee[2]]:
                                        temp_index = 0
                                        while len(for_ongoing_start_list[callee[2]]) < 2 and len(for_first_conditional_list[callee[2]]) > temp_index:
                                            for_ongoing_start_list[callee[2]].append(for_first_conditional_list[callee[2]][temp_index])
                                            temp_index += 1
                                        for_ongoing_end_list[callee[2]].append(for_first_conditional_list[callee[2]][-1])
                                elif callee[1] == 'while':
                                    while_ongoing_start[callee[2]] = 1
                                    if while_conditional_list[callee[2]]:
                                        while_ongoing_start_list[callee[2]].append(while_conditional_list[callee[2]][0])
                                        # Since the first element is a lambda, also add the next actual function
                                        if len(while_conditional_list[callee[2]]) > 1:
                                            while_ongoing_start_list[callee[2]].append(while_conditional_list[callee[2]][1])    
                                        while_ongoing_end_list[callee[2]].append(while_conditional_list[callee[2]][-1])
                                else:
                                    print_error_for_make_function('WHILE_CONTROL error1')
                            else:
                                if callee[1] == 'while conditional':
                                    while_conditional_start[callee[2]] = 0
                                elif callee[1] == 'for conditional first':
                                    for_first_conditional_start[callee[2]] = 0
                                elif callee[1] == 'for conditional second':
                                    for_second_conditional_start[callee[2]] = 0
                                elif callee[1] == 'while':
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                        if prev_callee_list:
                                            # If the while loop ends without any separate function call (e.g., ends inside an if-statement),
                                            # the last called function from the if-statement should be used to fill the ending point.
                                            while_ongoing_end_list[callee[2]].clear()
                                            for temp_prev_callee in prev_callee_list:
                                                if temp_prev_callee[9] >= control_flow_information_list[-1][3] and temp_prev_callee[9] <= callee[3]:
                                                    # Only consider functions that occurred within the same while loop
                                                    while_working_list[callee[2]] = 1
                                                    while_ongoing_end_list[callee[2]].append(temp_prev_callee)
                                            # If no separate function call occurred inside the while loop,
                                            # the first element is a lambda (placeholder), so we need to replace it
                                            if len(while_ongoing_start_list[callee[2]]) < 2:
                                                for temp_prev_callee in prev_callee_list:
                                                    if temp_prev_callee[9] >= control_flow_information_list[-1][3] and temp_prev_callee[9] <= callee[3]:
                                                        if while_ongoing_start_list[callee[2]][0] != temp_prev_callee:
                                                            while_ongoing_start_list[callee[2]].append(temp_prev_callee)

                                    # If both start and end points of the while loop exist, connect them
                                    if iteration_ongoing_break[callee[2]] == 0:
                                        if while_ongoing_start_list[callee[2]] and while_ongoing_end_list[callee[2]]:
                                            make_connect(call_graph_matrix,call_graph_function_pos,caller,while_ongoing_end_list[callee[2]],while_ongoing_start_list[callee[2]][0])
                                        elif not while_ongoing_start_list[callee[2]] and not while_ongoing_end_list[callee[2]]:
                                            if while_working_list[callee[2]] == 1:
                                                print_error_for_make_function('while control working error2')
                                        # If only one of start/end exists, it's also a structural error
                                        else:
                                            print(while_ongoing_start_list[callee[2]],while_ongoing_end_list[callee[2]],while_working_list[callee[2]])
                                            print_error_for_make_function('while control working error3')                                       
                                    # If a 'continue' statement exists, connect it to the beginning of the while loop
                                    if iteration_continue_list[callee[2]]:
                                        for temp_iteration_continue in iteration_continue_list[callee[2]]:
                                            make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_iteration_continue,while_ongoing_start_list[callee[2]][0])
                                    # Reset any connections related to lambda functions (placeholders)
                                    iteration_lambda_function_name = control_flow_iteration_lambda_function[control_flow_iteration_lambda_function_pos]
                                    input_to_iteration_lambda_list = []
                                    output_to_iteration_lambda_list = []
                                    for iteration_index in range(0,len(call_graph_matrix[caller][iteration_lambda_function_name])):
                                        if call_graph_matrix[caller][iteration_lambda_function_name][iteration_index] == 1:
                                            output_to_iteration_lambda_list.append(reverse_call_graph_function_pos[iteration_index])
                                            call_graph_matrix[caller][iteration_lambda_function_name][iteration_index] = 0

                                    for function_name, function_call_matrix in call_graph_matrix[caller].items():
                                        if function_call_matrix[call_graph_function_pos[caller][iteration_lambda_function_name]] == 1:
                                            input_to_iteration_lambda_list.append([function_name])
                                            function_call_matrix[call_graph_function_pos[caller][iteration_lambda_function_name]] = 0
                                            
                                    for temp_output_to_interation in output_to_iteration_lambda_list:
                                        if iteration_lambda_function_name != temp_output_to_interation:
                                            make_connect(call_graph_matrix,call_graph_function_pos,caller,input_to_iteration_lambda_list,[temp_output_to_interation])

                                    for temp_index in range(0,len(while_ongoing_end_list[callee[2]])):
                                        if while_ongoing_end_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            while_ongoing_end_list[callee[2]].pop(temp_index)
                                            break
                                    for temp_index in range(0,len(while_conditional_list[callee[2]])):
                                        if while_conditional_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            while_conditional_list[callee[2]].pop(temp_index)
                                            break
                                    for temp_index in range(0,len(while_ongoing_start_list[callee[2]])):
                                        if while_ongoing_start_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            while_ongoing_start_list[callee[2]].pop(temp_index)
                                            break
                                            
                                    for temp_index in range(0,len(iteration_break_list[callee[2]])):
                                        if iteration_break_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            iteration_break_list[callee[2]].pop(temp_index)
                                            break

                                    for temp_index in range(0,len(iteration_continue_list[callee[2]])):
                                        if iteration_continue_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            iteration_continue_list[callee[2]].pop(temp_index)
                                            break

                                    if not while_conditional_list[callee[2]] and not while_ongoing_start_list[callee[2]] and not while_ongoing_end_list[callee[2]]:
                                        while_working_list[callee[2]] = 0

                                    if while_conditional_list[callee[2]]:
                                        control_flow_can_empty = 0

                                    # Logic for populating prev_callee_list before passing to the next step
                                    prev_callee_list.clear()

                                    # If a function exists in the condition part of the while loop
                                    if while_conditional_list[callee[2]]:
                                        prev_callee_list.clear()
                                        if function_not_in_list(while_conditional_list[callee[2]][-1],prev_callee_list) == 0:
                                            prev_callee_list.append(while_conditional_list[callee[2]][-1])   

                                    # If there is no function in the condition part, connect from the end of the while loop to the next step
                                    if while_ongoing_end_list[callee[2]] and not while_conditional_list[callee[2]]:
                                        for temp_while_ongoing_end in while_ongoing_end_list[callee[2]]:
                                            if function_not_in_list(temp_while_ongoing_end,prev_callee_list) == 0:
                                                prev_callee_list.append(temp_while_ongoing_end)

                                    # If there is no function in the condition part, connect from before the while loop to the next step
                                    if while_prev_start_list[callee[2]] and not while_conditional_list[callee[2]]:
                                        for temp_while_prev_start in while_prev_start_list[callee[2]]:
                                            if function_not_in_list(temp_while_prev_start,prev_callee_list) == 0:
                                                prev_callee_list.append(temp_while_prev_start)
                                    
                                    if iteration_break_list[callee[2]]:
                                        for temp_iteration_break_list in iteration_break_list[callee[2]]:
                                            for temp_iteration_break in temp_iteration_break_list:
                                                if function_not_in_list(temp_iteration_break,prev_callee_list) == 0:
                                                    prev_callee_list.append(temp_iteration_break)

                                    if iteration_continue_list[callee[2]]:
                                        if not while_conditional_list[callee[2]]:
                                            for temp_iteration_continue_list in iteration_continue_list[callee[2]]:
                                                for temp_iteration_continue in temp_iteration_continue_list:
                                                    if function_not_in_list(temp_iteration_continue,prev_callee_list) == 0:
                                                        prev_callee_list.append(temp_iteration_continue)

                                    
                                    while_ongoing_start[callee[2]] = 0
                                    while_ongoing_start_list[callee[2]].clear()
                                    while_prev_start_list[callee[2]].clear()

                                    while_conditional_list[callee[2]].clear()
                                    while_ongoing_end_list[callee[2]].clear()
                                    iteration_break_list[callee[2]].clear()
                                    iteration_ongoing_break[callee[2]] = 0
                                    iteration_continue_list[callee[2]].clear()

                                    if control_flow_can_empty == 1:
                                        control_flow_can_empty = control_flow_can_empty_list[-1]
                                    control_flow_can_empty_list.pop()
                                    if control_flow_list:
                                        if while_working_list[callee[2]] == 1:
                                            control_flow_end = 1
                                        else:
                                            control_flow_end = control_flow_end_list[-1]
                                        control_flow_end_list.pop()
                                        if control_flow_list.pop() != WHILE_CONTROL:
                                            print('control flow is not if control!!')
                                        control_flow_information_list.pop()
                                    while_working_list[callee[2]] = 0
                                    control_flow_iteration_lambda_function_pos -= 1
                                elif callee[1] == 'for':
                                    if control_flow_end == 1:   
                                        control_flow_end = 0
                                        if prev_callee_list:
                                            for_ongoing_end_list[callee[2]].clear() # If control flow ends before the while statement ends and there are no other function calls
                                            for temp_prev_callee in prev_callee_list:
                                                if temp_prev_callee[9] >= control_flow_information_list[-1][3] and temp_prev_callee[9] <= callee[3]:
                                                    while_working_list[callee[2]] = 1
                                                    for_ongoing_end_list[callee[2]].append(temp_prev_callee)
                                            if len(for_ongoing_start_list[callee[2]]) < 2:     # When there is no separate function call inside the while statement
                                                for temp_prev_callee in prev_callee_list:
                                                    if temp_prev_callee[9] >= control_flow_information_list[-1][3] and temp_prev_callee[9] <= callee[3]:
                                                        if for_ongoing_start_list[callee[2]][0] != temp_prev_callee:
                                                            for_ongoing_start_list[callee[2]].append(temp_prev_callee)

                                    if iteration_continue_list[callee[2]]:  # If 'continue' is present, it connects to the condition part of the for statement.
                                        if for_second_conditional_list[callee[2]]:
                                            for temp_iteration_continue in iteration_continue_list[callee[2]]:
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_iteration_continue,for_second_conditional_list[callee[2]][0])
                                        elif for_first_conditional_list[callee[2]]:
                                            for temp_iteration_continue in iteration_continue_list[callee[2]]:
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_iteration_continue,for_first_conditional_list[callee[2]][0])
                                            
                                    prev_callee_list.clear()
                                    if iteration_ongoing_break[callee[2]] == 0:
                                        if for_first_conditional_list[callee[2]] and for_second_conditional_list[callee[2]]:
                                            if len(for_second_conditional_list[callee[2]]) > 1:
                                                for temp_index in range(0,len(for_second_conditional_list[callee[2]])-1):
                                                    prev_callee_list.append(for_second_conditional_list[callee[2]][temp_index])
                                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,for_second_conditional_list[callee[2]][temp_index+1])
                                                    prev_callee_list.clear()
                                            if for_ongoing_end_list[callee[2]] and for_ongoing_start_list[callee[2]]:
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,for_ongoing_end_list[callee[2]],for_second_conditional_list[callee[2]][0])
                                                prev_callee_list.clear()
                                                prev_callee_list.append(for_second_conditional_list[callee[2]][-1])
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,for_first_conditional_list[callee[2]][0])
                                                prev_callee_list.clear()
                                            else:
                                                print_error_for_make_function('for_first & for_second error')
                                        elif for_first_conditional_list[callee[2]] and not for_second_conditional_list[callee[2]]:
                                            if for_ongoing_end_list[callee[2]] and for_ongoing_start_list[callee[2]]:
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,for_ongoing_end_list[callee[2]],for_first_conditional_list[callee[2]][0])
                                            else:
                                                print_error_for_make_function('for_first & for_second error2')
                                        elif not for_first_conditional_list[callee[2]] and for_second_conditional_list[callee[2]]:
                                            if len(for_second_conditional_list[callee[2]]) > 1:
                                                for temp_index in range(0,len(for_second_conditional_list[callee[2]])-1):
                                                    prev_callee_list.append(for_second_conditional_list[callee[2]][temp_index])
                                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,for_second_conditional_list[callee[2]][temp_index+1])
                                                    prev_callee_list.clear()
                                            if for_ongoing_start_list[callee[2]] and for_ongoing_end_list[callee[2]]:
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,for_ongoing_end_list[callee[2]],for_second_conditional_list[callee[2]][0])
                                                prev_callee_list.clear()
                                                prev_callee_list.append(for_second_conditional_list[callee[2]][-1])
                                                for temp_for_ongoing_start in for_ongoing_start_list[callee[2]]:
                                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,temp_for_ongoing_start)
                                                prev_callee_list.clear()
                                            elif for_ongoing_start_list[callee[2]] or for_ongoing_end_list[callee[2]]:
                                                print_error_for_make_function('for_first & for_second error3')
                                        elif not for_first_conditional_list[callee[2]] and not for_second_conditional_list[callee[2]]:
                                            if for_ongoing_start_list[callee[2]] and for_ongoing_end_list[callee[2]]:
                                                for temp_for_ongoing_start in for_ongoing_start_list[callee[2]]:
                                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,for_ongoing_end_list[callee[2]],temp_for_ongoing_start)
                                                prev_callee_list.clear()
                                            elif for_ongoing_start_list[callee[2]] or for_ongoing_end_list[callee[2]]:
                                                print_error_for_make_function('for_first & for_second error4')
                                            else:
                                                if while_working_list[callee[2]] == 1:
                                                    print_error_for_make_function('for_first & for_second error5')
                                    elif iteration_continue_list[callee[2]]:
                                        if for_first_conditional_list[callee[2]] and for_second_conditional_list[callee[2]]:
                                            if len(for_second_conditional_list[callee[2]]) > 1:
                                                for temp_index in range(0,len(for_second_conditional_list[callee[2]])-1):
                                                    prev_callee_list.append(for_second_conditional_list[callee[2]][temp_index])
                                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,for_second_conditional_list[callee[2]][temp_index+1])
                                                    prev_callee_list.clear()
                                            if for_ongoing_end_list[callee[2]] and for_ongoing_start_list[callee[2]]:
                                                prev_callee_list.clear()
                                                prev_callee_list.append(for_second_conditional_list[callee[2]][-1])
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,for_first_conditional_list[callee[2]][0])
                                                prev_callee_list.clear()
                                            else:
                                                print_error_for_make_function('for_first & for_second error11')
                                        elif not for_first_conditional_list[callee[2]] and for_second_conditional_list[callee[2]]:
                                            if len(for_second_conditional_list[callee[2]]) > 1:
                                                for temp_index in range(0,len(for_second_conditional_list[callee[2]])-1):
                                                    prev_callee_list.append(for_second_conditional_list[callee[2]][temp_index])
                                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,for_second_conditional_list[callee[2]][temp_index+1])
                                                    prev_callee_list.clear()
                                            if for_ongoing_start_list[callee[2]] and for_ongoing_end_list[callee[2]]:
                                                prev_callee_list.clear()
                                                prev_callee_list.append(for_second_conditional_list[callee[2]][-1])
                                                for temp_for_ongoing_start in for_ongoing_start_list[callee[2]]:
                                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,temp_for_ongoing_start)
                                                prev_callee_list.clear()
                                            elif for_ongoing_start_list[callee[2]] or for_ongoing_end_list[callee[2]]:
                                                print_error_for_make_function('for_first & for_second error12')
                                        elif not for_first_conditional_list[callee[2]] and not for_second_conditional_list[callee[2]]:
                                            if for_ongoing_start_list[callee[2]] and for_ongoing_end_list[callee[2]]:
                                                for temp_for_ongoing_start in for_ongoing_start_list[callee[2]]:
                                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,for_ongoing_end_list[callee[2]],temp_for_ongoing_start)
                                                prev_callee_list.clear()
                                            elif for_ongoing_start_list[callee[2]] or for_ongoing_end_list[callee[2]]:
                                                print_error_for_make_function('for_first & for_second error13')
                                            else:
                                                if while_working_list[callee[2]] == 1:
                                                    print_error_for_make_function('for_first & for_second error14')
                                    
                                    iteration_lambda_function_name = control_flow_iteration_lambda_function[control_flow_iteration_lambda_function_pos]
                                    input_to_iteration_lambda_list = []
                                    output_to_iteration_lambda_list = []
                                    for iteration_index in range(0,len(call_graph_matrix[caller][iteration_lambda_function_name])):
                                        if call_graph_matrix[caller][iteration_lambda_function_name][iteration_index] == 1:
                                            output_to_iteration_lambda_list.append(reverse_call_graph_function_pos[iteration_index])
                                            call_graph_matrix[caller][iteration_lambda_function_name][iteration_index] = 0
                                    for function_name, function_call_matrix in call_graph_matrix[caller].items():
                                        if function_call_matrix[call_graph_function_pos[caller][iteration_lambda_function_name]] == 1:
                                            input_to_iteration_lambda_list.append([function_name])
                                            function_call_matrix[call_graph_function_pos[caller][iteration_lambda_function_name]] = 0
                                    for temp_output_to_interation in output_to_iteration_lambda_list:
                                        if iteration_lambda_function_name != temp_output_to_interation:
                                            make_connect(call_graph_matrix,call_graph_function_pos,caller,input_to_iteration_lambda_list,[temp_output_to_interation])

                                    for temp_index in range(0,len(for_first_conditional_list[callee[2]])):
                                        if for_first_conditional_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            for_first_conditional_list[callee[2]].pop(temp_index)
                                            break
                                    for temp_index in range(0,len(for_ongoing_start_list[callee[2]])):
                                        if for_ongoing_start_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            for_ongoing_start_list[callee[2]].pop(temp_index)
                                            break
                                    for temp_index in range(0,len(for_ongoing_end_list[callee[2]])):
                                        if for_ongoing_end_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            for_ongoing_end_list[callee[2]].pop(temp_index)
                                            break

                                    for temp_index in range(0,len(iteration_break_list[callee[2]])):
                                        if iteration_break_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            iteration_break_list[callee[2]].pop(temp_index)
                                            break

                                    for temp_index in range(0,len(iteration_continue_list[callee[2]])):
                                        if iteration_continue_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            iteration_continue_list[callee[2]].pop(temp_index)
                                            break

                                    if not for_first_conditional_list[callee[2]] and not for_ongoing_start_list[callee[2]] and not for_ongoing_end_list[callee[2]] and not for_second_conditional_list[callee[2]]:
                                        while_working_list[callee[2]] = 0

                                    # Set prev_callee_list to be passed to next
                                    if for_first_conditional_list[callee[2]]:
                                        control_flow_can_empty = 0
                                        
                                    prev_callee_list.clear()
                                    if for_first_conditional_list[callee[2]] and for_second_conditional_list[callee[2]]:
                                        if for_ongoing_end_list[callee[2]] and for_ongoing_start_list[callee[2]]:
                                            prev_callee_list.append(for_first_conditional_list[callee[2]][-1]) # After the for statement ends, the first and last conditional statements can be connected to the next.
                                        else:
                                            print_error_for_make_function('for_first & for_second error6')
                                    elif for_first_conditional_list[callee[2]] and not for_second_conditional_list[callee[2]]:
                                        if for_ongoing_end_list[callee[2]] and for_ongoing_start_list[callee[2]]:
                                            prev_callee_list.append(for_first_conditional_list[callee[2]][-1]) # After the for statement ends, the first and last conditional statements can be connected to the next.
                                        else:
                                            print_error_for_make_function('for_first & for_second error7')
                                    elif not for_first_conditional_list[callee[2]] and for_second_conditional_list[callee[2]]:
                                        prev_callee_list.append(for_second_conditional_list[callee[2]][-1])
                                        for temp_for_prev_start in for_prev_start_list[callee[2]]:
                                            prev_callee_list.append(temp_for_prev_start)
                                    elif not for_first_conditional_list[callee[2]] and not for_second_conditional_list[callee[2]]:
                                        if for_ongoing_start_list[callee[2]] and for_ongoing_end_list[callee[2]]:
                                            for temp_for_prev_start in for_prev_start_list[callee[2]]:
                                                prev_callee_list.append(temp_for_prev_start)
                                            for temp_for_ongoing_end in for_ongoing_end_list[callee[2]]:
                                                prev_callee_list.append(temp_for_ongoing_end)
                                        elif for_ongoing_start_list[callee[2]] or for_ongoing_end_list[callee[2]]:
                                            print(for_ongoing_start_list[callee[2]],for_ongoing_end_list[callee[2]])
                                            print_error_for_make_function('for_first & for_second error9')
                                        else:
                                            if while_working_list[callee[2]] == 1:
                                                print_error_for_make_function('for_first & for_second error10')

                                    if not prev_callee_list:
                                        for temp_for_prev_start in for_prev_start_list[callee[2]]:
                                            prev_callee_list.append(temp_for_prev_start)

                                    if iteration_break_list[callee[2]]:
                                        for temp_iteration_break_list in iteration_break_list[callee[2]]:
                                            for temp_iteration_break in temp_iteration_break_list:
                                                if function_not_in_list(temp_iteration_break,prev_callee_list) == 0:
                                                    prev_callee_list.append(temp_iteration_break)
                                    
                                    if iteration_continue_list[callee[2]]:
                                        if not for_first_conditional_list[callee[2]] and not for_second_conditional_list[callee[2]]:
                                            for temp_iteration_continue_list in iteration_continue_list[callee[2]]:
                                                for temp_iteration_continue in temp_iteration_continue_list:
                                                    if function_not_in_list(temp_iteration_continue,prev_callee_list) == 0:
                                                        prev_callee_list.append(temp_iteration_continue)



                                    for_ongoing_start[callee[2]] = 0
                                    for_ongoing_start_list[callee[2]].clear()
                                    for_ongoing_end_list[callee[2]].clear()   
                                    for_first_conditional_list[callee[2]].clear()
                                    for_second_conditional_list[callee[2]].clear()
                                    for_prev_start_list[callee[2]].clear()
                                    iteration_break_list[callee[2]].clear()
                                    iteration_continue_list[callee[2]].clear()
                                    iteration_ongoing_break[callee[2]] = 0
                                    if control_flow_can_empty == 1:
                                        control_flow_can_empty = control_flow_can_empty_list[-1]
                                    control_flow_can_empty_list.pop()
                                    if control_flow_list:
                                        if while_working_list[callee[2]] == 1:
                                            control_flow_end = 1
                                        else:
                                            control_flow_end = control_flow_end_list[-1]
                                        control_flow_end_list.pop()
                                        if control_flow_list.pop() != WHILE_CONTROL:
                                            print('control flow is not if control!!')
                                        control_flow_information_list.pop()
                                    while_working_list[callee[2]] = 0
                                    control_flow_iteration_lambda_function_pos -= 1

                        
                        elif control_flow_return_val == DO_WHILE_CONTROL:
                            if callee[0] == 'start_info':
                                if callee[1] == 'do_while':
                                    control_flow_can_empty_list.append(control_flow_can_empty)
                                    control_flow_can_empty = 1
                                    control_flow_iteration_lambda_function_pos += 1
                                    control_flow_end_list.append(control_flow_end)
                                    control_flow_end = 0
                                    control_flow_list.append(control_flow_return_val)
                                    control_flow_information_list.append(callee)
                                    control_flow_level += 1
                                    do_while_ongoing_start[callee[2]] = 1
                                    for temp_prev_callee in prev_callee_list:
                                        do_while_prev_start_list[callee[2]].append(temp_prev_callee)
                                elif callee[1] == 'do_while conditional':
                                    do_while_ongoing_start[callee[2]] = 0
                                    do_while_conditional_start[callee[2]] = 1
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                        if control_flow_can_empty == 0:
                                            do_while_function_call_normal_list[callee[2]] = 1
                                        if prev_callee_list:
                                            do_while_ongoing_end_list[callee[2]].clear()
                                            for temp_prev_callee in prev_callee_list:
                                                if temp_prev_callee[11] >= control_flow_information_list[-1][3] and temp_prev_callee[11] <= callee[3]:  # Only for what occurs within the same do while statement
                                                    do_while_working_list[callee[2]] = 1
                                                    do_while_ongoing_end_list[callee[2]].append(temp_prev_callee)
                                            if not do_while_ongoing_start_list[callee[2]]:
                                                for temp_prev_callee in prev_callee_list:
                                                    if temp_prev_callee[11] >= control_flow_information_list[-1][3] and temp_prev_callee[11] <= callee[3]:  # Only for what occurs within the same do_while statement
                                                        do_while_ongoing_start_list[callee[2]].append(temp_prev_callee)
                                    # Since control_flow is unlikely to occur in the do_while conditional part, it should not be initialized to 0.
                                    if do_while_ongoing_break[callee[2]] == 1 and do_while_continue_list[callee[2]]:
                                        prev_callee_list.clear()
                                        for temp_do_while_continue_list in do_while_continue_list[callee[2]]:
                                            for temp_do_while_continue in temp_do_while_continue_list:
                                                if function_not_in_list(temp_do_while_continue,prev_callee_list) == 0:
                                                    prev_callee_list.append(temp_do_while_continue)
                                    elif do_while_ongoing_break[callee[2]] == 1:
                                        print_error_for_make_function('do_while_ongoing_break error1')
                                else:
                                    print_error_for_make_function('do_while control error')
                            else:
                                if callee[1] == 'do_while':
                                    if control_flow_end == 1:
                                        print_error_for_make_function('control_flow_end == 1?')

                                    if do_while_continue_list[callee[2]]:  # If continue is present, it connects to the very beginning
                                        if do_while_conditional_list[callee[2]]:
                                            for temp_do_while_continue in do_while_continue_list[callee[2]]:
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_do_while_continue,do_while_conditional_list[callee[2]][0])

                                    prev_callee_list.clear()
                                    if do_while_conditional_list[callee[2]]:
                                        if do_while_ongoing_start_list[callee[2]] and do_while_ongoing_end_list[callee[2]]:
                                            for temp_do_while_ongoing_start in do_while_ongoing_start_list[callee[2]]:
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,do_while_ongoing_end_list[callee[2]],temp_do_while_ongoing_start)
                                        elif not do_while_ongoing_start_list[callee[2]] and do_while_ongoing_end_list[callee[2]]:
                                            prev_callee_list.append(do_while_ongoing_end_list[callee[2]][-1])
                                            make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,do_while_conditional_list[callee[2]][0])
                                        else:
                                            print_error_for_make_function('do_while error1')
                                    else:
                                        if do_while_ongoing_start_list[callee[2]] and do_while_ongoing_end_list[callee[2]]:
                                            for temp_do_while_ongoing_start in do_while_ongoing_start_list[callee[2]]:
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,do_while_ongoing_end_list[callee[2]],temp_do_while_ongoing_start)
                                        elif not do_while_ongoing_start_list[callee[2]] and not do_while_ongoing_end_list[callee[2]]:
                                            if do_while_working_list[callee[2]] == 1:
                                                print_error_for_make_function('do_while error2')
                                        else:
                                            print(do_while_ongoing_start_list[callee[2]],do_while_ongoing_end_list[callee[2]])
                                            print_error_for_make_function('do_while error3')

                                    iteration_lambda_function_name = control_flow_iteration_lambda_function[control_flow_iteration_lambda_function_pos]
                                    input_to_iteration_lambda_list = []
                                    output_to_iteration_lambda_list = []
                                    for iteration_index in range(0,len(call_graph_matrix[caller][iteration_lambda_function_name])):
                                        if call_graph_matrix[caller][iteration_lambda_function_name][iteration_index] == 1:
                                            output_to_iteration_lambda_list.append(reverse_call_graph_function_pos[iteration_index])
                                            call_graph_matrix[caller][iteration_lambda_function_name][iteration_index] = 0
                                    #[jvds)
                                    for function_name, function_call_matrix in call_graph_matrix[caller].items():
                                        if function_call_matrix[call_graph_function_pos[caller][iteration_lambda_function_name]] == 1:
                                            input_to_iteration_lambda_list.append([function_name])
                                            function_call_matrix[call_graph_function_pos[caller][iteration_lambda_function_name]] = 0
                                    for temp_output_to_interation in output_to_iteration_lambda_list:
                                        if iteration_lambda_function_name != temp_output_to_interation:
                                            make_connect(call_graph_matrix,call_graph_function_pos,caller,input_to_iteration_lambda_list,[temp_output_to_interation])

                                    for temp_index in range(0,len(do_while_ongoing_start_list[callee[2]])):
                                        if do_while_ongoing_start_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            do_while_ongoing_start_list[callee[2]].pop(temp_index)
                                            break
                                    for temp_index in range(0,len(do_while_ongoing_end_list[callee[2]])):
                                        if do_while_ongoing_end_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            do_while_ongoing_end_list[callee[2]].pop(temp_index)
                                            break

                                    for temp_index in range(0,len(do_while_break_list[callee[2]])):
                                        if do_while_break_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            do_while_break_list[callee[2]].pop(temp_index)
                                            break

                                    for temp_index in range(0,len(do_while_continue_list[callee[2]])):
                                        if do_while_continue_list[callee[2]][temp_index][-1] == iteration_lambda_function_name:
                                            do_while_continue_list[callee[2]].pop(temp_index)
                                            break

                                    if not do_while_ongoing_start_list[callee[2]] and not do_while_ongoing_end_list[callee[2]] and not do_while_conditional_list[callee[2]]:
                                        do_while_working_list[callee[2]] = 0

                                    if do_while_function_call_normal_list[callee[2]] == 1: # If a function is called even once inside a do while statement
                                        control_flow_can_empty = 0
                                
                                    prev_callee_list.clear()
                                    if not do_while_ongoing_start_list[callee[2]] and not do_while_ongoing_end_list[callee[2]]:
                                        for temp_prev_callee in do_while_prev_start_list[callee[2]]:
                                            prev_callee_list.append(temp_prev_callee)
                                    elif do_while_ongoing_end_list[callee[2]]:
                                        for temp_do_while_ongoing_end in do_while_ongoing_end_list[callee[2]]:
                                            prev_callee_list.append(temp_do_while_ongoing_end)
                                    else:
                                        print_error_for_make_function('do_while error4')

                                    if do_while_break_list[callee[2]]:
                                        for temp_do_while_break_list in do_while_break_list[callee[2]]:
                                            for temp_do_while_break in temp_do_while_break_list:
                                                if function_not_in_list(temp_do_while_break,prev_callee_list) == 0:
                                                    prev_callee_list.append(temp_do_while_break)

                                    if do_while_continue_list[callee[2]]:
                                        if not do_while_conditional_list[callee[2]]:
                                            for temp_do_while_continue_list in do_while_continue_list[callee[2]]:
                                                for temp_do_while_continue in temp_do_while_continue_list:
                                                    if function_not_in_list(temp_do_while_continue,prev_callee_list) == 0:
                                                        prev_callee_list.append(temp_do_while_continue)

                                    do_while_function_call_normal_list[callee[2]] = 0
                                    do_while_ongoing_start[callee[2]] = 0
                                    do_while_conditional_start[callee[2]] = 0
                                    do_while_ongoing_start_list[callee[2]].clear()
                                    do_while_ongoing_end_list[callee[2]].clear()
                                    do_while_conditional_list[callee[2]].clear()
                                    do_while_prev_start_list[callee[2]].clear()
                                    do_while_break_list[callee[2]].clear()
                                    do_while_ongoing_break[callee[2]] = 0
                                    do_while_continue_list[callee[2]].clear()
                        
                                    if control_flow_can_empty == 1:
                                        control_flow_can_empty = control_flow_can_empty_list[-1]
                                    control_flow_can_empty_list.pop()
                                    if control_flow_list:
                                        if  do_while_working_list[callee[2]] == 1:
                                            control_flow_end = 1
                                        else:
                                            control_flow_end = control_flow_end_list[-1]
                                        control_flow_end_list.pop() 
                                        if control_flow_list.pop() != DO_WHILE_CONTROL:
                                            print('control flow is not do_while control!!')
                                        control_flow_information_list.pop()
                                    do_while_working_list[callee[2]] = 0
                                    control_flow_iteration_lambda_function_pos -= 1
                                else:
                                    print_error_for_make_function('do_while control error2')

                        elif control_flow_return_val == BREAK_CONTROL:
                            if control_flow_list and control_flow_information_list:
                                break_check = 0
                                for temp_index in range(len(control_flow_list) - 1, -1, -1):
                                    if control_flow_list[temp_index] == WHILE_CONTROL:
                                        iteration_break_list[control_flow_information_list[temp_index][2]].append(prev_callee_list.copy())
                                        break_check = 1
                                        if temp_index == len(control_flow_list) - 1:
                                            iteration_ongoing_break[control_flow_information_list[temp_index][2]] = 1
                                            control_flow_skip_list.append((WHILE_CONTROL,control_flow_information_list[temp_index],'break'))
                                            control_flow_skip = 1
                                        break
                                    elif control_flow_list[temp_index] == DO_WHILE_CONTROL:
                                        do_while_break_list[control_flow_information_list[temp_index][2]].append(prev_callee_list.copy())
                                        break_check = 1
                                        if not do_while_continue_list[control_flow_information_list[temp_index][2]]:
                                            if temp_index == len(control_flow_list) - 1:
                                                do_while_ongoing_break[control_flow_information_list[temp_index][2]] = 1
                                                control_flow_skip_list.append((DO_WHILE_CONTROL,control_flow_information_list[temp_index],'break'))
                                                control_flow_skip = 1
                                        else:
                                            if temp_index == len(control_flow_list) - 1:
                                                do_while_ongoing_break[control_flow_information_list[temp_index][2]] = 1
                                                control_flow_skip_list.append((DO_WHILE_CONTROL,control_flow_information_list[temp_index],'continue'))
                                                control_flow_skip = 1
                                        break
                                    elif control_flow_list[temp_index] == SWITCH_CONTROL:
                                        switch_break_list[control_flow_information_list[temp_index][2]].append(prev_callee_list.copy())
                                        switch_ongoing_break_if[control_flow_information_list[temp_index][2]] = 1
                                        break_check = 1
                                        if temp_index == len(control_flow_list) - 1:
                                            switch_ongoing_break[control_flow_information_list[temp_index][2]] = 1
                                            control_flow_skip_list.append((SWITCH_CONTROL,control_flow_information_list[temp_index],'break'))
                                            control_flow_skip = 1
                                        break
                                if break_check == 0:
                                    print_error_for_make_function('break control error1')
                            else:
                                print_error_for_make_function('break control error2')

                        elif control_flow_return_val == CONTINUE_CONTROL:
                            if control_flow_list and control_flow_information_list:
                                continue_check = 0
                                for temp_index in range(len(control_flow_list) - 1, -1, -1):
                                    if control_flow_list[temp_index] == WHILE_CONTROL:
                                        iteration_continue_list[control_flow_information_list[temp_index][2]].append(prev_callee_list.copy())
                                        continue_check = 1
                                        if temp_index == len(control_flow_list) - 1:
                                            control_flow_skip_list.append((WHILE_CONTROL,control_flow_information_list[temp_index],'continue'))
                                            control_flow_skip = 1
                                        break
                                    elif control_flow_list[temp_index] == DO_WHILE_CONTROL:
                                        do_while_continue_list[control_flow_information_list[temp_index][2]].append(prev_callee_list.copy())
                                        continue_check = 1
                                        if temp_index == len(control_flow_list) - 1:
                                            control_flow_skip_list.append((DO_WHILE_CONTROL,control_flow_information_list[temp_index],'continue'))
                                            control_flow_skip = 1
                                        break
                                if continue_check == 0:
                                    print_error_for_make_function('continue control error1')
                            else:
                                print_error_for_make_function('continue control error2')

                        elif control_flow_return_val == RETURN_CONTROL:
                            if control_flow_list and control_flow_information_list:
                                if control_flow_list[-1] == IF_CONTROL:
                                    control_flow_skip_list.append((RETURN_CONTROL,1,IF_CONTROL,control_flow_information_list[-1][2]))
                                    if_return_list[control_flow_information_list[-1][2]].append(prev_callee_list.copy())
                                    if_return_cul_list[control_flow_information_list[-1][2]].append(prev_callee_list.copy())
                                    control_flow_skip = 1
                                elif control_flow_list[-1] == SWITCH_CONTROL:
                                    control_flow_skip_list.append((RETURN_CONTROL,1,SWITCH_CONTROL,control_flow_information_list[-1][2]))
                                    switch_return_list[control_flow_information_list[-1][2]].append(prev_callee_list.copy())
                                    switch_return_cul_list[control_flow_information_list[-1][2]].append(prev_callee_list.copy())
                                    control_flow_skip = 1
                                else:
                                    print_error_for_make_function('return exist in for/while/do_while why?')
                            else:
                                control_flow_skip_list.append((RETURN_CONTROL,0))   # skip to the end
                                control_flow_skip = 1
                                
                if FOR_DEVELOPMENT == 1:
                    print_matrix(call_graph_matrix,call_graph_function_pos,caller)
                if while_check == 0:
                    for temp_lambda_function_name in control_flow_iteration_lambda_function:
                        del call_graph_matrix[caller][temp_lambda_function_name]
                        del call_graph_function_pos[caller][temp_lambda_function_name]
                    for temp_function_name, temp_graph_list in call_graph_matrix[caller].items():
                        del temp_graph_list[:MAX_SINGLE_CONTROL_FLOW_COUNT]
                if while_check == 0:
                    if call_graph_matrix[caller]['S'][call_graph_function_pos[caller]['E']-MAX_SINGLE_CONTROL_FLOW_COUNT] == 1:
                        not_call_function_set.add(caller)
        if while_check == 1:
            all_combinations_index += 1
        if all_combinations_index == len(all_combinations):
            for caller, callees in function_graph.items():
                if caller in function_not_call:
                    continue
                for temp_lambda_function_name in control_flow_iteration_lambda_function:
                    del call_graph_matrix[caller][temp_lambda_function_name]
                    del call_graph_function_pos[caller][temp_lambda_function_name]
                for temp_function_name, temp_graph_list in call_graph_matrix[caller].items():
                    del temp_graph_list[:MAX_SINGLE_CONTROL_FLOW_COUNT]

            for caller, callees in function_graph.items():
                if caller in function_not_call:
                    continue
                call_graph_matrix_list.append(call_graph_matrix[caller].copy())
                call_graph_function_pos_list.append(call_graph_function_pos[caller].copy())
                caller_list.append(caller)
            break
            
        if while_check == 0 and not_call_function_set == prev_not_call_function_set:
            while_check = 1
            for r in range(1, len(prev_not_call_function_set) + 1):
                all_combinations.extend([set(c) for c in combinations(prev_not_call_function_set, r)])
            call_graph_matrix = {}
            call_graph_function_pos = {}
            call_graph_function_start  = {}
            call_graph_function_end = {}
            call_graph_matrix_list = []
            call_graph_function_pos_list = []
            caller_list = []
            not_call_function_set.clear()
        elif while_check == 0:
            prev_not_call_function_set = not_call_function_set.copy()
    return     call_graph_matrix_list, call_graph_function_pos_list, caller_list, prev_not_call_function_set
                
def print_call_graph(function_graph):
    print("Function Call Graph:")
    call_length = 0
    for caller, callees in function_graph.items():
        if callees:
            for callee in callees:
                if 'end_info' != callee[0] and 'start_info' != callee[0]:
                    print(f"{caller} -> {callee[0],callee[1],callee[2],callee[3],callee[4],callee[5],callee[6],callee[7],callee[8],callee[9],callee[10],callee[11],callee[12],callee[13],callee[-1]}")
                    call_length += 1
                else:
                    if callee[1] != 'break' and callee[1] != 'continue' and callee[1] != 'return':
                        print(f"{caller} -> {callee[0],callee[1],callee[2],callee[3]}")
                    else:
                        print(f"{caller} -> {callee[0],callee[1],callee[2]}")
        else:
            print(f"{caller} -> (End)")
    print("Function Call Graph Lengh: "+str(call_length))

def merge_all_graphs(call_graph_matrix_list,call_graph_function_pos_list,caller_list,user_functions,all_functions,not_call_function_set,output_file_name):

    user_functions_list = list(user_functions)
    visited_dict = {}

    call_graph_matrix_use_name = {}
    for temp_index in range(0,len(call_graph_matrix_list)):
        adj_matrix = call_graph_matrix_list[temp_index]
        caller = caller_list[temp_index]
        visited_dict[caller] = set([])
        keys = list(adj_matrix.keys())

        graph_dict = {}
        for src, row in adj_matrix.items():
            if src in ('S', 'E'):
                continue
            graph_dict[src] = [keys[i] for i, val in enumerate(row) if val == 1]

        start_list = [keys[i] for i, val in enumerate(adj_matrix['S']) if val == 1]
        start_set = set(start_list)
        end_index = keys.index('E')
        end_list = [src for src, row in adj_matrix.items() if row[end_index] == 1]
        end_set = set(end_list)
        call_graph_matrix_use_name[caller] = []
        call_graph_matrix_use_name[caller].append(start_set)
        call_graph_matrix_use_name[caller].append(end_set)
        call_graph_matrix_use_name[caller].append(graph_dict)
    
    call_graph_matrix_use_name_copy = {}
    for function_name, temp_call_graph_matrix_use_name in call_graph_matrix_use_name.items():
        user_func_check = 0
        call_graph_matrix_use_name_copy[function_name] = []
        call_graph_matrix_use_name_copy[function_name].append(temp_call_graph_matrix_use_name[0].copy())
        call_graph_matrix_use_name_copy[function_name].append(temp_call_graph_matrix_use_name[1].copy())
        call_graph_matrix_use_name_copy[function_name].append(temp_call_graph_matrix_use_name[2].copy())

        visited_dict[function_name] = set([])
        while True:
            temp_start_set = set([])
            for func in call_graph_matrix_use_name_copy[function_name][0]:
                if func in visited_dict[function_name]:
                    continue
                if func in user_functions_list:
                    temp_start_set = temp_start_set.union(call_graph_matrix_use_name[func][0])
                    visited_dict[function_name].add(func)
                else:
                    temp_start_set.add(func)
            if temp_start_set == call_graph_matrix_use_name_copy[function_name][0]:
                call_graph_matrix_use_name_copy[function_name][0].discard('E')
                break
            call_graph_matrix_use_name_copy[function_name][0] = temp_start_set.copy()

        visited_dict[function_name] = set([])
        while True:
            temp_end_set = set([])
            for func in call_graph_matrix_use_name_copy[function_name][1]:
                if func in visited_dict[function_name]:
                    continue
                if func in user_functions_list:
                    temp_end_set = temp_end_set.union(call_graph_matrix_use_name[func][1])
                    visited_dict[function_name].add(func)
                else:
                    temp_end_set.add(func)
            if temp_end_set == call_graph_matrix_use_name_copy[function_name][1]:
                call_graph_matrix_use_name_copy[function_name][1].discard('S')
                break
            call_graph_matrix_use_name_copy[function_name][1] = temp_end_set.copy()    

    for function_name, temp_call_graph_matrix_use_name in call_graph_matrix_use_name.items():
        
        for src,dst in temp_call_graph_matrix_use_name[2].items():
            visited_dict[function_name] = set([])
            while True:
                temp_set = set([])
                for func in call_graph_matrix_use_name_copy[function_name][2][src]:
                    if func in visited_dict[function_name]:
                        continue
                    if func in user_functions_list:
                        temp_set = temp_set.union(call_graph_matrix_use_name[func][0])
                        visited_dict[function_name].add(func)
                    else:
                        temp_set.add(func)
                    temp_set.discard('E')
                if call_graph_matrix_use_name_copy[function_name][2][src] == temp_set:
                    break
                call_graph_matrix_use_name_copy[function_name][2][src] = temp_set.copy()
 
    call_functions = all_functions - user_functions
    user_functions_list = list(user_functions)
    call_functions_list = list(call_functions)
    merged_call_graph_matrix = []
    merged_call_graph_matrix_name = []
    merged_call_graph_matrix_pos = {}        
    for temp_all_function_name in call_functions_list:
        merged_call_graph_matrix.append([0] * len(call_functions_list))
        merged_call_graph_matrix_name.append(temp_all_function_name)
    for temp_index in range(0,len(merged_call_graph_matrix_name)):
        merged_call_graph_matrix_pos[merged_call_graph_matrix_name[temp_index]] = temp_index
    for function_name, temp_call_graph_matrix_use_name_copy in call_graph_matrix_use_name_copy.items():
        for src, dst in temp_call_graph_matrix_use_name_copy[2].items():
            if src in user_functions_list:
                for temp_src in call_graph_matrix_use_name_copy[src][1]:
                    for temp_dst in dst:
                        merged_call_graph_matrix[merged_call_graph_matrix_pos[temp_src]][merged_call_graph_matrix_pos[temp_dst]] = 1
            else:
                for temp_dst in dst:
                    merged_call_graph_matrix[merged_call_graph_matrix_pos[src]][merged_call_graph_matrix_pos[temp_dst]] = 1

    make_graph_using_gui_use_list(merged_call_graph_matrix_name,merged_call_graph_matrix,output_file_name)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="C file analyzer with optional graph generation.")
    parser.add_argument("c_file", help="Path to the C source file")
    parser.add_argument("-g", "--graph", action="store_true", help="Generate graph from adjacency matrix")
    parser.add_argument("-m", "--merge", action="store_true", help="Merge all function graphs into a single graph")
    parser.add_argument("-o", "--output", metavar="OUTPUT", help="Specify output file name")

    args = parser.parse_args()

    c_file = args.c_file
    make_graph = args.graph
    merge_graph = args.merge
    output_file_name = args.output
    output_opt = 1 if output_file_name else 0

    if not os.path.isfile(c_file):
        print(f"Error: File '{c_file}' not found.")
        exit(1)

    function_graph, user_functions, all_functions = extract_if_depth(c_file)
    function_not_call = extract_function_not_call_function(c_file)
    print_call_graph(function_graph)
    call_graph_matrix_list, call_graph_function_pos_list, caller_list, not_call_function_set = make_matrix_from_function_graph(function_graph,function_not_call)

    if make_graph:
        make_graph_using_gui(call_graph_matrix_list,call_graph_function_pos_list,caller_list)
    if merge_graph:
        merge_all_graphs(call_graph_matrix_list, call_graph_function_pos_list, caller_list, user_functions, all_functions, not_call_function_set,output_file_name)
