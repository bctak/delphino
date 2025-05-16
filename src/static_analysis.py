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
DO_WHILE_CONTROL = 0b1000
BREAK_CONTROL = 0b10000
CONTINUE_CONTROL = 0b100000
RETURN_CONTROL = 0b1000000

FOR_DEVELOPMENT = 0

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
        # 함수 호출 내역이 없다면 라이브러리 호출도 없는 것으로 간주
        return False
    
    if func_name in visited:
        return False  # 이미 방문한 함수는 다시 검사하지 않음 (무한 루프 방지)

    visited.add(func_name)

    for callee in function_calls[func_name]:
        if callee not in user_functions:
            return True  # 라이브러리 함수 호출 발견
        if calls_library_function(callee, user_functions, function_calls, visited):
            return True  # 하위 호출 중 라이브러리 호출 발견

    return False

def extract_function_not_call_function(file_path):
    """Extract the function call graph from a C source file using Clang AST dump."""
    try:
        result = subprocess.run(["clang", "-Xclang", "-ast-dump", "-fsyntax-only", file_path], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # 본문 시작 위치 찾기
        code_body_start = get_code_body_start(result.stdout, file_path)
        if code_body_start is None:
            print("Warning: Could not determine the code body start position.")
            return {}

        user_functions, all_functions = extract_function_calls_with_clang(file_path)  # 사용자 정의 함수 목록
        function_calls = {}  # 호출 관계 저장 (caller -> [callee1, callee2, ...])
        current_function = None  # 현재 분석 중인 함수

        lines = result.stdout.split("\n")[code_body_start:]  # 본문 부분만 분석

        for i in range(len(lines)):
            line = lines[i]

            # 현재 어떤 함수 내부인지 찾기 (FunctionDecl 사용)
            if "FunctionDecl" in line:
                match = re.search(r"FunctionDecl\s+[^\']+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*'", line)
                if match:
                    current_function = match.group(1)
                    if current_function in user_functions:
                        function_calls[current_function] = []  # 함수 호출 그래프 초기화

            # 함수 호출 찾기 (CallExpr -> DeclRefExpr 사용)
            elif "CallExpr" in line and current_function:
                call_depth = get_first_alpha_or_angle_index(line)
                for j in range(i + 1, min(i + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                    if "DeclRefExpr" in lines[j]:
                        match = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[j])
                        if match:
                            called_function = match.group(1)
                            if called_function in all_functions or called_function in user_functions:  # 사용자 정의 함수만 추적
                                function_calls[current_function].append(called_function)
                            if "clone" == called_function:
                                for k in range(j + 1, min(j + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                                    if "DeclRefExpr" in lines[k]:
                                        match2 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match2:
                                            cloned_function = match2.group(1)
                                            if cloned_function in all_functions or cloned_function in user_functions:
                                                function_calls[current_function].append(called_function)
                                            break
                            if "pthread_create" == called_function:
                                for k in range(j + 1, min(j + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
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
        
        print("사용자 함수 중 라이브러리 함수를 호출하지 않는 함수들:", user_functions_not_call)
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


def extract_multi_function(file_path):
    """
    Extract a sequential function workflow as a directed graph.
    Functions are treated as nodes, and edges indicate sequential calls.
    """
    try:
        result = subprocess.run(["clang", "-Xclang", "-ast-dump", "-fsyntax-only", file_path], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        code_body_start = get_code_body_start(result.stdout, file_path)
        if code_body_start is None:
            print("Warning: Could not determine the code body start position.")
            return []

        lines = result.stdout.split("\n")[code_body_start:]
        function_stack = []  # 실행 순서를 추적하기 위한 스택
        edges = []  # Directed edges (caller -> callee)

        for i in range(len(lines)):
            line = lines[i]

            # 함수 호출 탐지 (CallExpr -> DeclRefExpr)
            if "CallExpr" in line:
                call_depth = get_first_alpha_or_angle_index(line)  # 알파벳이 처음 등장하는 위치로 깊이 설정
                function_stack.append((call_depth, None))  # 임시 자리 추가

                for j in range(i + 1, min(i + 10, len(lines))):  # Look for function names
                    if "DeclRefExpr" in lines[j]:
                        match = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[j])
                        if match:
                            called_function = match.group(1)
                            function_stack[-1] = (call_depth, called_function)
                        break

        # 함수 실행 순서를 정리하면서 Directed Edge 생성
        sorted_stack = sorted(function_stack, key=lambda x: x[0])  # 깊이 기준 정렬 (알파벳이 먼저 등장한 순서)
        execution_order = [func for depth, func in sorted_stack if func]

        for i in range(len(execution_order) - 1):
            edges.append((execution_order[i], execution_order[i + 1]))

        return edges
    except Exception as e:
        print(f"Error extracting function workflow: {e}")
        return []


def extract_function_workflow(file_path):
    """
    Extract a sequential function workflow as a directed graph.
    Functions are treated as nodes, and edges indicate sequential calls.
    """
    try:
        result = subprocess.run(["clang", "-Xclang", "-ast-dump", "-fsyntax-only", file_path], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        code_body_start = get_code_body_start(result.stdout, file_path)
        if code_body_start is None:
            print("Warning: Could not determine the code body start position.")
            return []

        lines = result.stdout.split("\n")[code_body_start:]
        function_stack = []  # 실행 순서를 추적하기 위한 스택
        edges = []  # Directed edges (caller -> callee)

        for i in range(len(lines)):
            line = lines[i]

            # 함수 호출 탐지 (CallExpr -> DeclRefExpr)
            if "CallExpr" in line:
                call_depth = get_first_alpha_or_angle_index(line)  # 알파벳이 처음 등장하는 위치로 깊이 설정
                print(call_depth)
                function_stack.append((call_depth, None))  # 임시 자리 추가

                for j in range(i + 1, min(i + 10, len(lines))):  # Look for function names
                    if "DeclRefExpr" in lines[j]:
                        match = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[j])
                        if match:
                            called_function = match.group(1)
                            function_stack[-1] = (call_depth, called_function)
                        break

        # 함수 실행 순서를 정리하면서 Directed Edge 생성
        sorted_stack = sorted(function_stack, key=lambda x: x[0])  # 깊이 기준 정렬 (알파벳이 먼저 등장한 순서)
        execution_order = [func for depth, func in sorted_stack if func]

        for i in range(len(execution_order) - 1):
            edges.append((execution_order[i], execution_order[i + 1]))

        return edges
    except Exception as e:
        print(f"Error extracting function workflow: {e}")
        return []

def print_error(s,lines,i):
    print(s+'!!!')
    print(lines[i])
    print()
    for j in range(0,9):
        print(lines[i-10+j])
    for j in range(0,10):
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


def extract_glibc_functions_from_c_code(file_path):
    """Extract glibc function calls from a given C source file using Clang AST."""
    user_functions, all_functions = extract_function_calls_with_clang(file_path)
    glibc_functions = get_glibc_functions()
    used_glibc_functions = all_functions.intersection(glibc_functions)
    print(user_functions)
    return used_glibc_functions

def make_function_call_sequence(function_calls_sequence,):
    print('hi')

def make_graph_using_gui(call_graph_matrix_list,call_graph_function_pos_list,caller_list):
    if len(call_graph_matrix_list) == len(call_graph_function_pos_list) and len(call_graph_function_pos_list) == len(caller_list):
        for i in range(0,len(call_graph_function_pos_list)):
            call_graph_matrix = call_graph_matrix_list[i]
            call_graph_function_pos = call_graph_function_pos_list[i]
            caller = caller_list[i]
            adj_matrix = []
            node_names = []
            
            for function_name, function_call_matrix in call_graph_matrix.items():
                node_names.append(function_name)
                adj_matrix.append(function_call_matrix)
            # Graphviz 그래프 생성
            dot = Digraph(format='pdf')
            dot.attr(rankdir='TB', size='8,5')  # 좌→우 방향, 크기 조정

            # 노드 추가
            for name in node_names:
                dot.node(name, shape='ellipse', style='filled', fillcolor='lightblue',penwidth='2.5')

            # 엣지 추가
            num_nodes = len(adj_matrix)
            for i in range(num_nodes):
                for j in range(num_nodes):
                    if adj_matrix[i][j] != 0:
                        dot.edge(node_names[i], node_names[j])

            # PDF 파일로 저장
            dot.render(str(caller), cleanup=True)  # graphviz_graph.pdf 생성
    else:
        print_error_for_make_function('length error')
    
def make_graph_using_gui_use_list(adj_matrix_name,adj_matrix): 
    node_names = adj_matrix_name
    
    # Graphviz 그래프 생성
    dot = Digraph(format='pdf')
    dot.attr(rankdir='TB', size='8,5')  # 좌→우 방향, 크기 조정

    # 노드 추가
    for name in node_names:
        dot.node(name, shape='ellipse', style='filled', fillcolor='lightblue',penwidth='2.5')

    # 엣지 추가
    num_nodes = len(adj_matrix)
    for i in range(num_nodes):
        for j in range(num_nodes):
            if adj_matrix[i][j] != 0:
                dot.edge(node_names[i], node_names[j])

    # PDF 파일로 저장
    dot.render(str('FINAL GRAPH'), cleanup=True)  # graphviz_graph.pdf 생성





def extract_if_depth(file_path):
    """Extract the function call graph from a C source file using Clang AST dump."""
    try:
        result = subprocess.run(["clang", "-Xclang", "-ast-dump", "-fsyntax-only", file_path], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # 본문 시작 위치 찾기
        code_body_start = get_code_body_start(result.stdout, file_path)
        if code_body_start is None:
            print("Warning: Could not determine the code body start position.")
            return {}

        user_functions, all_functions = extract_function_calls_with_clang(file_path)  # 사용자 정의 함수 목록
        function_calls = {}  # 호출 관계 저장 (caller -> [callee1, callee2, ...])
        current_function = None  # 현재 분석 중인 함수

        function_calls_sequence = {} #시퀀스가 반영된 함수 호출관계

        lines = result.stdout.split("\n")[code_body_start:]  # 본문 부분만 분석


        current_depth = 0
        function_call_ongoing = 0
        function_stack = []
        function_call_depth = []
        current_call_depth = 0

        has_else_list = [0] * 100
        has_else = 0
        if_first_ongoing = 0     #IfStmt를 처음으로 감지하는 부분, if의 조건문부분을 탐지하기 위해 사용
        if_conditional_ongoing = 0
        if_conditional_depth = 0 #IfStmt조건문 부분을 탐지하기 위해 사용, if 조건문 부분이 depth가 다시 처음으로 동일해지는 부분을 캐치
        if_conditional_depth_list = []
        return_ongoing_depth_list = []
        return_ongoing_depth = 0


        if_ongoing_depth = []
        current_has_else = 0
        if_level = 0
        if_level_else_if_list = [] #else if문들을 wide하게 표현하기 위해 사용되는 리스트
        if_level_else_if_list.append(0)
        if_level_else_if_list_not_append = 0
        if_level_else_if_list_not_append_list = [0] * 100
        if_level_not_plus = 0
        if_new_check = 0
        if_level_not_plus_list = [0] * 100

        conditional_operator_first_ongoing = 0
        conditional_operator_ongoing = 0
        conditional_operator_ongoing_list = []
        first_conditional_operator_depth = 0
        first_conditional_operator_depth_list =[]
        conditional_ongoing_depth = []
        conditional_if_level_plus = 0

        if_level_else_if_list_not_append_conditional = 0

        #반복문을 위한 변수들

        while_new_check = 0
        while_level = 0
        while_depth_list = []
        while_depth = 0
        while_ongoing = 0
        while_ongoing_depth = []
        while_first_ongoing = 0

        #while_new_check = 0
        #while_level = 0
        for_depth_list = []
        for_conditional_list =[0] * 100
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
        switch_case_list = [] #switch case문들을 wide하게 표현하기 위해 사용되는 리스트
        switch_case_list.append(0)
        switch_not_append = 0
        switch_new_check = 0
        switch_first_ongoing = 0
        switch_break = 0

        control_flow_list = []
        control_flow_re_check = 0

        control_flow_iteration_lambda_function = []
        control_flow_iteration_lambda_function_pos = 0
        for i in range(0,100):
            control_flow_iteration_lambda_function.append('iteration lambda function' + str(i))

        
        lines.append('End of File!!')   #if return does not exist
        for i in range(len(lines)):
            line = lines[i]
            current_depth = get_first_alpha_or_angle_index(line)
            #print(line)

            #함수가 한줄에 여러개 있을때 처리하는 로직  
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
                    if not function_call_depth:
                        break    
            while control_flow_list:
                temp_control_flow = control_flow_list[-1]
                if temp_control_flow == IF_CONTROL:
                    if if_ongoing_depth:
                        while current_depth <= if_ongoing_depth[len(if_ongoing_depth)-1]:
                            if if_level <= 0:
                                print_error('if_level error1',lines,i)
                            function_calls[current_function].append(('end_info','if',if_level,if_new_check))
                            if_level_not_plus_list[if_level] = 0
                            if_level_else_if_list_not_append_list[if_level] = 0
                            #if has_else_list[if_level]:
                            #    print_error('has_else_list why full?',lines,i)
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
                            if not if_ongoing_depth:
                                break
                            

                    #삼항 연산 조건문

                    if conditional_ongoing_depth:
                        while current_depth <= conditional_ongoing_depth[-1]:
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
                            if not conditional_ongoing_depth:
                                break    
                elif temp_control_flow == WHILE_CONTROL:
                    if while_ongoing_depth:
                        while current_depth <= while_ongoing_depth[-1]:
                            if while_level <= 0:
                                print_error('while level error4',lines,i)
                            function_calls[current_function].append(('end_info','while',while_level,while_new_check))
                            control_flow_iteration_lambda_function_pos -= 1
                            while_level -= 1
                            if while_new_check > 1000:
                                while_new_check = 0
                            while_new_check += 1
                            while_ongoing_depth.pop()   
                            while_depth_list.pop()
                            control_flow_re_check = 1
                            control_flow_list.pop()
                            if not while_ongoing_depth:
                                break       

                    if for_ongoing_depth:
                        while current_depth <= for_ongoing_depth[-1]:
                            if while_level <= 0:
                                print_error('while level error5',lines,i)
                            function_calls[current_function].append(('end_info','for',while_level,while_new_check))
                            for_conditional_list[while_level] = 0
                            control_flow_iteration_lambda_function_pos -= 1
                            while_level -= 1
                            if while_new_check > 1000:
                                while_new_check = 0
                            while_new_check += 1
                            for_ongoing_depth.pop()   
                            for_depth_list.pop()
                            control_flow_re_check = 1
                            control_flow_list.pop()
                            if not for_ongoing_depth:
                                break   
                elif temp_control_flow == DO_WHILE_CONTROL:
                    if do_while_ongoing_depth:
                        while current_depth <= do_while_ongoing_depth[-1]:
                            if do_while_level <= 0:
                                print_error('while level error6',lines,i)
                            function_calls[current_function].append(('end_info','do_while',do_while_level,do_while_new_check))
                            control_flow_iteration_lambda_function_pos -= 1
                            do_while_level -= 1
                            do_while_ongoing_depth.pop()   
                            do_while_depth_list.pop()
                            if do_while_new_check > 1000:
                                do_while_new_check = 0
                            do_while_new_check += 1
                            control_flow_re_check = 1
                            control_flow_list.pop()
                            if not do_while_ongoing_depth:
                                break    
                elif temp_control_flow == SWITCH_CONTROL:
                    if switch_ongoing_depth:
                        while current_depth <= switch_ongoing_depth[-1]:
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
                            if not switch_ongoing_depth:
                                break   
                elif temp_control_flow == RETURN_CONTROL:
                    if return_ongoing_depth_list:
                        while current_depth <= return_ongoing_depth_list[-1]:
                            function_calls[current_function].append(('end_info','return',1))
                            return_ongoing_depth_list.pop()
                            control_flow_list.pop()
                            control_flow_re_check = 1
                            if not return_ongoing_depth_list:
                                break
                else:
                    print_error('error!!!',lines,i)
                if control_flow_re_check == 1:
                    control_flow_re_check = 0
                else:
                    break
                   

            #탈출 구문들 위에

            #반복문 while 진입 시작
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
                    if while_new_check >= 1000: #실제로 반복문 내부로 들어왔을 때만 증가하게
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
            #for문 시작

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
                        control_flow_list.append(WHILE_CONTROL)
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
            
            #do-while문 시작
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
            

            #반복문 끝


            #조건문 진입 시작

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
                        if if_level_else_if_list and len(if_level_else_if_list) > if_level: #1번째가 실제로 if_level 1단계이다.
                            if_level_else_if_list[if_level] += 1
                            function_calls[current_function].append(('start_info','else',if_level,if_new_check))
                        conditional_operator_ongoing = 0
                else:
                    conditional_operator_ongoing = 2
                    conditional_operator_ongoing_list.append(conditional_operator_ongoing)


            #일반 조건문
            if if_conditional_depth_list:
                if_conditional_depth = if_conditional_depth_list[-1]    #마지막 가져오기
            else:
                if_conditional_depth = -1

            #if has_else_list[if_level] == 1:
                #print(has_else_list)
            #    has_else = has_else_list[if_level][-1]
            if has_else_list[if_level] == 1 and if_level == len(if_conditional_depth_list):
                temp_if_end_depth = get_first_backtick_index(line)
                if temp_if_end_depth + 2 == if_conditional_depth:
                    #print_for_debug(lines,i,20)
                    #print(if_conditional_depth_list,has_else_list,if_level,'###')
                    #print('if_level',if_level)
                    #print_for_debug(lines,i,20)
                    if if_level <= 0:
                        print_error('if_level error2',lines,i)
                    if if_level_else_if_list and len(if_level_else_if_list) > if_level: #1번째가 실제로 if_level 1단계이다.
                        if_level_else_if_list[if_level] += 1
                    else:
                        print_error('if_level_else_if_list error2 !!!!',lines,i)
                    #print(if_level_else_if_list,if_level)
                    if "IfStmt" in line:
                        if_ongoing_depth.pop()
                        #if_level -= 1
                        if_level_not_plus_list[if_level] = 1
                        #if_level_not_plus = 1
                        function_calls[current_function].append(('start_info','else if',if_level,if_new_check))
                        #print('if_level else if',if_level)
                        #print('else if has_else',has_else_list)
                        #print(function_calls[current_function])
                        #print_for_debug(lines,i,20)
                        if_level_else_if_list_not_append_list[if_level] = 1
                    else:
                        function_calls[current_function].append(('start_info','else',if_level,if_new_check))
                        #print('if_level else',if_level)
                        #print('else has_else',has_else_list)
                        #print(function_calls[current_function])
                        #print_for_debug(lines,i,20)
                    has_else_list[if_level] = 0
                    #if_conditional_depth_list.pop()
                    #if_level_else_if_list_not_append = 1

            if "IfStmt" in line:
                #print(if_level_not_plus_list,if_level_else_if_list_not_append_list,if_level)
                if if_level_else_if_list_not_append_list[if_level] == 0:
                    if_level_else_if_list.append(0)
                    if if_new_check >= 1000:
                        if_new_check = 0
                    if_new_check += 1
                else:
                    if_level_else_if_list_not_append_list[if_level] = 0
                if "has_else" in line:
                    #has_else = 1
                    #print('if_level has_else',if_level)
                    #print('else has_else',has_else_list)
                    #print('has else')
                    #print('has else',if_level)
                    #print_for_debug(lines,i,20)
                    if if_level_not_plus_list[if_level] == 1:
                        has_else_list[if_level] = 1
                    else: #새로운 if라는 뜻
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
                    #print_for_debug(lines,i,20)
                    #print(if_conditional_depth_list,has_else_list,if_level,'!!!')
                    if_conditional_ongoing = 1
                else:
                    if_first_ongoing = 2

            if if_conditional_ongoing > 0:
                if if_conditional_ongoing == 2:
                    if current_depth == if_conditional_depth:
                        #print('hi')
                        #print(if_level_not_plus_list,if_level)
                        #print_for_debug(lines,i,20)
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

            #조건문 관련 끝 if_level을 통해서만 조절  

            #switch-case start

            if switch_depth_list:
                switch_depth = switch_depth_list[-1]    #마지막 가져오기
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

            # 현재 어떤 함수 내부인지 찾기 (FunctionDecl 사용)
            if "FunctionDecl" in line:
                match = re.search(r"FunctionDecl\s+[^\']+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*'", line)
                if match:
                    current_function = match.group(1)
                    if current_function in user_functions:
                        function_calls[current_function] = []  # 함수 호출 그래프 초기화
                        if_new_check = 0
                        while_new_check = 0
                        do_while_new_check = 0
                        break_stat = 0
                        continue_stat = 0
                        switch_new_check = 0

            #함수에 대해서 처리
            if "CallExpr" in line and current_function:    
                temp_function_call_depth = get_first_alpha_or_angle_index(line)
                function_call_depth.append(temp_function_call_depth)
                current_call_depth = temp_function_call_depth

            # 함수 호출 찾기 (CallExpr -> DeclRefExpr 사용) (함수 안에 함수까지 전부 탐지 완료)
            if "CallExpr" in line and current_function:
                for j in range(i + 1, min(i + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                    if "DeclRefExpr" in lines[j]:
                        match = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[j])
                        if match:
                            called_function = match.group(1)
                            if called_function in all_functions or called_function in user_functions:  # 사용자 정의 함수만 추적
                                #if called_function == 'srand':
                                #    print(str(if_level), 'srand!!')
                                #print_for_debug(lines,i,10)
                                #print(if_level_else_if_list,if_level,called_function,has_else)
                                #print(len(if_level_else_if_list),if_level)
                                #print('여기여기')
                                function_stack.append([current_call_depth,i,if_level,if_level_else_if_list[if_level],if_new_check,switch_level,switch_case_list[switch_level],switch_new_check,while_level,while_new_check,do_while_level,do_while_new_check,break_stat,continue_stat,called_function])
                                break_stat = 0
                                continue_stat = 0
                                #print(called_function,function_call_depth,function_stack)
                                #function_calls[current_function].append([call_depth,i,called_function])
                            if "clone" == called_function:
                                for k in range(j + 1, min(j + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                                    if "DeclRefExpr" in lines[k]:
                                        match2 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match2:
                                            cloned_function = match2.group(1)
                                            if cloned_function in all_functions or cloned_function in user_functions:
                                                function_stack.append([current_call_depth,i,if_level,if_level_else_if_list[if_level],if_new_check,switch_level,switch_case_list[switch_level],switch_new_check,while_level,while_new_check,do_while_level,do_while_new_check,break_stat,continue_stat,cloned_function])
                                                #function_calls[current_function].append([0,i,cloned_function])
                                                break_stat = 0
                                                continue_stat = 0
                                            break
                            if "pthread_create" == called_function:
                                for k in range(j + 1, min(j + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                                    if "DeclRefExpr" in lines[k]:
                                        match3 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match3:
                                            pthread_create_function = match3.group(1)
                                            if pthread_create_function in all_functions or pthread_create_function in user_functions:
                                                #function_calls[current_function].append([0,i,pthread_create_function])
                                                function_stack.append([current_call_depth,i,if_level,if_level_else_if_list[if_level],if_new_check,switch_level,switch_case_list[switch_level],switch_new_check,while_level,while_new_check,do_while_level,do_while_new_check,break_stat,continue_stat,pthread_create_function])
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
                

    
        #print(if_conditional_depth_list)
        #exit(0)
        return function_calls,user_functions,all_functions
    except Exception as e:
        print(f"Error extracting function call graph: {e}")
        print(e)
        return {}


def extract_function_call_graph(file_path):
    """Extract the function call graph from a C source file using Clang AST dump."""
    try:
        result = subprocess.run(["clang", "-Xclang", "-ast-dump", "-fsyntax-only", file_path], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # 본문 시작 위치 찾기
        code_body_start = get_code_body_start(result.stdout, file_path)
        if code_body_start is None:
            print("Warning: Could not determine the code body start position.")
            return {}

        user_functions, all_functions = extract_function_calls_with_clang(file_path)  # 사용자 정의 함수 목록
        function_calls = {}  # 호출 관계 저장 (caller -> [callee1, callee2, ...])
        current_function = None  # 현재 분석 중인 함수

        lines = result.stdout.split("\n")[code_body_start:]  # 본문 부분만 분석
        
        current_depth = 0
        function_call_ongoing = 0
        function_stack = []
        function_call_depth = 0

        if_ongoing = 0
        if_call_depth = 0
        if_level = 0
        has_else = 0
        is_one_line_if = 0 #한줄 if문일경우 CompoundStmt가 안나타남을 알았다.

        for i in range(len(lines)):
            line = lines[i]
            current_depth = get_first_alpha_or_angle_index(line)
            
            #함수가 한줄에 여러개 있을때 처리하는 로직
            if function_call_ongoing == 1 and current_depth <= function_call_depth :  
                if function_stack and current_function:
                    for j in reversed(range(0, len(function_stack))):
                        function_calls[current_function].append(function_stack[j])
                function_stack = []
                function_call_ongoing = 0

            if if_level > 0 and current_depth < if_call_depth:
                if_level -= 1
                if_call_depth = current_depth
            

            # 현재 어떤 함수 내부인지 찾기 (FunctionDecl 사용)
            if "FunctionDecl" in line:
                match = re.search(r"FunctionDecl\s+[^\']+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*'", line)
                if match:
                    current_function = match.group(1)
                    if current_function in user_functions:
                        function_calls[current_function] = []  # 함수 호출 그래프 초기화

            # `이 있으면 그 depth는 마무리라는 뜻이다.
            #if (조건문이 있을 경우 어떻게 순서를 정할지 정하는 로직)
            if "IfStmt" in line and current_function:
                if_level += 1
                if_call_depth = current_depth

                print('gogo sing')
                #CompoundStmt


            if "ConditionalOperator" in line and current_function:
                print('gogogo')

            # 함수 호출 찾기 (CallExpr -> DeclRefExpr 사용) (함수 안에 함수까지 전부 탐지 완료)
            if "CallExpr" in line and current_function:    
                function_call_depth = get_first_alpha_or_angle_index(line)
                if function_call_ongoing == 0:
                    function_call_ongoing = 1
                for j in range(i + 1, min(i + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                    if "DeclRefExpr" in lines[j]:
                        match = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[j])
                        if match:
                            called_function = match.group(1)
                            if called_function in all_functions or called_function in user_functions:  # 사용자 정의 함수만 추적
                                function_stack.append([function_call_depth,i,called_function])
                                #function_calls[current_function].append([call_depth,i,called_function])
                            if "clone" == called_function:
                                for k in range(j + 1, min(j + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                                    if "DeclRefExpr" in lines[k]:
                                        match2 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match2:
                                            cloned_function = match2.group(1)
                                            if cloned_function in all_functions or cloned_function in user_functions:
                                                function_stack.append([function_call_depth,i,cloned_function])
                                                #function_calls[current_function].append([0,i,cloned_function])
                                            break
                            if "pthread_create" == called_function:
                                for k in range(j + 1, min(j + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                                    if "DeclRefExpr" in lines[k]:
                                        match3 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match3:
                                            pthread_create_function = match3.group(1)
                                            if pthread_create_function in all_functions or pthread_create_function in user_functions:
                                                #function_calls[current_function].append([0,i,pthread_create_function])
                                                function_stack.append([function_call_depth,i,pthread_create_function])
                                            break
                        break

        if function_stack and current_function:
            for i in reversed(range(0, len(function_stack))):
                function_calls[current_function].append(function_stack[i])            

        return function_calls
    except Exception as e:
        print(f"Error extracting function call graph: {e}")
        return {}


def extract_function_call_graph_old(file_path):
    """Extract the function call graph from a C source file using Clang AST dump."""
    try:
        result = subprocess.run(["clang", "-Xclang", "-ast-dump", "-fsyntax-only", file_path], 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # 본문 시작 위치 찾기
        code_body_start = get_code_body_start(result.stdout, file_path)
        if code_body_start is None:
            print("Warning: Could not determine the code body start position.")
            return {}

        user_functions, all_functions = extract_function_calls_with_clang(file_path)  # 사용자 정의 함수 목록
        function_calls = {}  # 호출 관계 저장 (caller -> [callee1, callee2, ...])
        current_function = None  # 현재 분석 중인 함수

        lines = result.stdout.split("\n")[code_body_start:]  # 본문 부분만 분석

        for i in range(len(lines)):
            line = lines[i]

            # 현재 어떤 함수 내부인지 찾기 (FunctionDecl 사용)
            if "FunctionDecl" in line:
                match = re.search(r"FunctionDecl\s+[^\']+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*'", line)
                if match:
                    current_function = match.group(1)
                    if current_function in user_functions:
                        function_calls[current_function] = []  # 함수 호출 그래프 초기화

            # 함수 호출 찾기 (CallExpr -> DeclRefExpr 사용)
            elif "CallExpr" in line and current_function:
                call_depth = get_first_alpha_or_angle_index(line)
                for j in range(i + 1, min(i + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                    if "DeclRefExpr" in lines[j]:
                        match = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[j])
                        if match:
                            called_function = match.group(1)
                            if called_function in all_functions or called_function in user_functions:  # 사용자 정의 함수만 추적
                                function_calls[current_function].append([call_depth,i,called_function])
                            if "clone" == called_function:
                                for k in range(j + 1, min(j + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                                    if "DeclRefExpr" in lines[k]:
                                        match2 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match2:
                                            cloned_function = match2.group(1)
                                            if cloned_function in all_functions or cloned_function in user_functions:
                                                function_calls[current_function].append([call_depth,i,called_function])
                                            break
                            if "pthread_create" == called_function:
                                for k in range(j + 1, min(j + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                                    if "DeclRefExpr" in lines[k]:
                                        match3 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match3:
                                            pthread_create_function = match3.group(1)
                                            if pthread_create_function in all_functions or pthread_create_function in user_functions:
                                                function_calls[current_function].append([call_depth,i,called_function])
                                            break
                        break

        return function_calls
    except Exception as e:
        print(f"Error extracting function call graph: {e}")
        return {}
    
def control_flow_check(callee):
    control_return_val = 0
    #return val means
    # 0 : normal
    # 1 : if
    # 2 : switch
    # 4 : for, while
    # 8 : do-while
    # 16 : break
    # 32 : continue
    if 'end_info' != callee[0] and 'start_info' != callee[0]:
        #print('normal!!')
        return NORMAL_CONTROL
    if callee[1] == 'if' or callee[1] == 'conditional' or callee[1] == 'else if' or callee[1] == 'else':
        #print('if!!')
        control_return_val |= IF_CONTROL
    if callee[1] == 'switch' or callee[1] == 'case' or callee[1] =='default':
        #print('switch!!')
        control_return_val |= SWITCH_CONTROL
    if callee[1] == 'for' or callee[1] =='while' or callee[1] == 'while conditional' or callee[1] == 'for conditional first' or callee[1] == 'for conditional second':
        #print('while!!')
        control_return_val |= WHILE_CONTROL
    if callee[1] == 'do_while' or callee[1] == 'do_while conditional':
        #print('do while!!')
        control_return_val |= DO_WHILE_CONTROL
    if callee[1] == 'break':
        #print('break!!')
        control_return_val |= BREAK_CONTROL
    if callee[1] == 'continue':
        #print('continue!!')
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
    """
    if control_flow_skip_list:
        if control_flow_skip_list[-1][0] == control_flow_return_val and callee[2] == control_flow_skip_list[-1][1][2]:
            return 0
    """
    return_val = prev_control_flow_skip
    if control_flow_skip_list:
        if control_flow_skip_list[-1][0] == control_flow_return_val:
            if control_flow_return_val == WHILE_CONTROL:
                if callee[2] == control_flow_skip_list[-1][1][2]:
                    control_flow_skip_list.pop()
                    return_val = 0
            elif control_flow_return_val == DO_WHILE_CONTROL:
                if control_flow_skip_list[-1][2] == 'break':     #break
                    if callee[0] == 'end_info' and callee[2] == control_flow_skip_list[-1][1][2]:
                        control_flow_skip_list.pop()
                        return_val = 0
                else:                                            #continue
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
    while True:
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
            call_graph_matrix[caller] = {}
            call_graph_function_pos[caller] = {}
            call_graph_function_start[caller] = []
            call_graph_function_end[caller] = []
            if callees:
                control_flow_iteration_lambda_function = []
                function_set = set([])
                function_set.add('S')
                function_set.add('E')
                function_list = []
                for i in range(0,100):
                    function_list.append('iteration lambda function' + str(i))
                    function_set.add('iteration lambda function' + str(i))
                    control_flow_iteration_lambda_function.append('iteration lambda function' + str(i))

                function_list.append('S')
                function_list.append('E')
                for callee in callees:
                    if control_flow_check(callee) == NORMAL_CONTROL:
                        if callee[-1] not in function_not_call:
                            if function_not_in_list_use_function_name(callee,function_list) == 0:
                                function_list.append(callee[-1])
                            function_set.add(callee[-1])
                index = 0

                #if len(function_list) == 102:
                #    continue
                if FOR_DEVELOPMENT == 1:
                    print(caller)
                for function_name in function_list:
                    if function_name not in function_not_call:
                        call_graph_matrix[caller][function_name] = [0]*len(function_list)
                        call_graph_function_pos[caller][function_name] = index
                        index += 1
                #for function_name, function_call_matrix in call_graph_matrix[caller].items():
                #    print(function_name,function_call_matrix)
                #print(call_graph_function_pos[caller])
                #기본 matrix 및 함수당 인덱스 매핑 완료

                prev_callee = (0,0,0,0,0,0,0,0,0,0,0,0,0,0,'S')
                prev_callee_list = []
                prev_callee_list.append(prev_callee)
                end_callee = (0,0,0,0,0,0,0,0,0,0,0,0,0,0,'E')
                if end_callee not in callees:
                    callees.append(end_callee)
                reverse_call_graph_function_pos = {value: key for key, value in call_graph_function_pos[caller].items()}

                start_make_matrix = 0
                for_prev_start = [[] for _ in range(100)] #for, while문 시작직전 함수 담는 스택
                for_after_start = [[] for _ in range(100)] #for, while문 시작직후 함수 담는 스택
                do_after_start = [[] for _ in range(100)] #do while문 시작직후 함수 담는 스택 (직전은 필요없음)
            


                control_flow_iteration_lambda_function_pos = -1
                control_flow_list = []  #control flow 정보를 담는 list
                control_flow = 0
                control_flow_end = 0
                control_flow_can_empty = 1 #0이 비어 있지 않을것 1이 빌 수도 있다.
                control_flow_can_empty_list = []
                control_flow_end_list = []
                control_flow_level = 0
                control_flow_information_list = []   #control flow의 처음 정보를 넣기 위한 리스트
                control_flow_skip = 0
                control_flow_skip_list = []
                control_flow_start_function_list = [] #control flow가 끝나고 start정보를 담는 리스트
                control_flow_end_function_list = [] #control flow가 끝나고 end정보를 담는 리스트

                if_prev_start_list = [[] for _ in range(100)] #if, 삼항 조건문 시작직전 함수 담는 스택
                if_ongoing_start_list = [[] for _ in range(100)] # else if문 함수 담는 스택
                if_ongoing_end_list = [[] for _ in range(100)]
                if_working_list = [0] * 100 #if문 내부에서 실제로 함수콜이 단한번이라도 있었는지 확인
                if_function_in_list = [0] * 100 #if문 안에 함수가 있었는지 확인하는 리스트
                if_function_in_final_list = [0] * 100 #if문안에 함수가 하나라도 없었으면 1로 바꿈
                if_ongoing_start_level_list = []
                if_prev_start = [0] * 100
                else_if_ongoing_start = [0] * 100
                else_ongoing_start = [0] * 100
                if_ongoing_start = [0] * 100
                if_return_list = [[] for _ in range(100)]
                if_return_cul_list = [[] for _ in range(100)]
                
                switch_prev_start_list = [[] for _ in range(100)] #switch case문 시작직전 함수 담는 스택
                switch_ongoing_start_list = [[] for _ in range(100)] #switch case문 중에 함수 담는 스택
                switch_ongoing_end_list = [[] for _ in range(100)]
                switch_working_list = [0] * 100 #if문 내부에서 실제로 함수콜이 단한번이라도 있었는지 확인
                switch_function_in_list = [0] * 100
                switch_function_in_final_list = [0] * 100
                switch_ongoing_start_level_list = []
                switch_prev_start = [0] * 100
                case_ongoing_start = [0] * 100
                default_ongoing_start = [0] * 100
                switch_ongoing_start = [0] * 100
                switch_break_list = [[] for _ in range(100)]
                switch_ongoing_break_if = [0] * 100
                switch_ongoing_break = [0] * 100
                switch_return_list = [[] for _ in range(100)]
                switch_return_cul_list = [[] for _ in range(100)]

                while_prev_start_list = [[] for _ in range(100)] #if, 삼항 조건문 시작직전 함수 담는 스택
                while_ongoing_start_list = [[] for _ in range(100)] # else if문 함수 담는 스택
                while_ongoing_end_list = [[] for _ in range(100)]
                while_function_in_list = [0] * 100 #if문 안에 함수가 있었는지 확인하는 리스트
                while_function_in_final_list = [0] * 100 #if문안에 함수가 하나라도 없었으면 1로 바꿈
                while_ongoing_start_level_list = []
                while_prev_start = [0] * 100
                #else_if_ongoing_start = [0] * 100
                #else_ongoing_start = [0] * 100
                while_ongoing_start = [0] * 100
                for_ongoing_start = [0] * 100
                #continue 때문에 조건내부에 함수가 있는지 확인해줘야함
                while_working_list = [0] * 100
                while_conditional_list = [[] for _ in range(100)] #while문 조건속에 함수가 존재할 때
                while_conditional_start = [0] * 100
                iteration_break_list = [[] for _ in range(100)]
                iteration_continue_list = [[] for _ in range(100)]
                iteration_ongoing_break = [0] * 100
                for_first_conditional_start = [0] * 100
                for_second_conditional_start = [0] * 100
                for_first_conditional_list = [[] for _ in range(100)] #for문 첫번째 조건부분에 함수가 존재할 때
                for_second_conditional_list = [[] for _ in range(100)] #for문 두번째 실행부분에 함수가 존재할 떄
                for_prev_start_list = [[] for _ in range(100)]
                for_ongoing_start_list = [[] for _ in range(100)]
                for_ongoing_end_list = [[] for _ in range(100)]

                do_while_ongoing_start = [0] * 100
                do_while_working_list = [0] * 100
                do_while_ongoing_start_list =  [[] for _ in range(100)] 
                do_while_conditional_list = [[] for _ in range(100)] 
                do_while_conditional_start = [0] * 100
                do_while_prev_start_list =  [[] for _ in range(100)] 
                do_while_ongoing_end_list = [[] for _ in range(100)] 
                do_while_function_call_normal_list = [0] * 100
                do_while_break_list = [[] for _ in range(100)] 
                do_while_continue_list = [[] for _ in range(100)] 
                do_while_ongoing_break = [0] * 100


                copy_callees = callees.copy()

                callee_index = 0
                not_call_combination_index = 0
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
                        if callee[-1] in function_not_call: #함수를 하나도 안호출하는 것은 애초에 제외
                            continue
                        if callee[-1] in not_call_function_set: #하나도 호출하지도 않을 수 있는 함수들은 하나씩 제외
                            continue
                        if not prev_callee_list:
                            print_error_for_make_function('prev_callee_list error1')
                        if control_flow_list:
                            control_flow = control_flow_list[-1]
                            #control_flow_start_function_current_list = control_flow_start_function_list[-1]
                            #control_flow_end_function_current_list = control_flow_end_function_list[-1]
                            if control_flow == IF_CONTROL:
                                if if_ongoing_start[callee[2]] == 1:
                                    if_working_list[callee[2]] = 1
                                    if_function_in_list[callee[2]] = 1  #if안에 함수가 하나라도 있었다.
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                    if else_if_ongoing_start[callee[2]] == 1:                   #else if가 나오고 처음이면 ongoing에 넣음
                                        else_if_ongoing_start[callee[2]] = 2
                                        if_ongoing_start_list[callee[2]][-1].append(callee)
                                        if_ongoing_end_list[callee[2]][-1].append(callee)
                                        #control_flow_start_function_current_list.append(callee)
                                        #control_flow_end_function_current_list.append(callee)
                                        #print('else if ongoing',prev_callee_list,'oo',callee)
                                        make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                    elif else_if_ongoing_start[callee[2]] == 2:
                                        #if_ongoing_start_list[callee[2]][-1] = callee #else if문의 마지막으로 호출된 함수를 최근으로 바꿔줌
                                        if if_ongoing_end_list[callee[2]]:
                                            if_ongoing_end_list[callee[2]][-1].clear()
                                            if_ongoing_end_list[callee[2]][-1].append(callee)
                                        else:
                                            print_error_for_make_function('if_ongoing_end_list')
                                        #control_flow_end_function_current_list[-1] = callee
                                        make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                            elif control_flow == SWITCH_CONTROL:
                                if switch_ongoing_start[callee[5]] == 1:
                                    switch_working_list[callee[5]] = 1
                                    switch_function_in_list[callee[5]] = 1  #if안에 함수가 하나라도 있었다.
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                    if case_ongoing_start[callee[5]] == 1:                   #else if가 나오고 처음이면 ongoing에 넣음
                                        case_ongoing_start[callee[5]] = 2
                                        #switch_ongoing_start_list[callee[5]].append(callee)
                                        switch_ongoing_end_list[callee[5]][-1].append(callee)
                                        #control_flow_start_function_current_list.append(callee)
                                        #control_flow_end_function_current_list.append(callee)
                                        make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                    elif case_ongoing_start[callee[5]] == 2:
                                        #switch_ongoing_start_list[callee[5]][-1] = callee #else if문의 마지막으로 호출된 함수를 최근으로 바꿔줌
                                        if switch_ongoing_end_list[callee[5]]:
                                            switch_ongoing_end_list[callee[5]][-1].clear()
                                            switch_ongoing_end_list[callee[5]][-1].append(callee)
                                        else:
                                            print_error_for_make_function('switch_ongoing_end_list')
                                        #control_flow_end_function_current_list[-1] = callee
                                        make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                            elif control_flow == WHILE_CONTROL:
                                while_working_list[callee[8]] = 1  
                                if control_flow_end == 1:
                                    control_flow_end = 0                               
                                if while_conditional_start[callee[8]] == 1: #while 조건문 내부에서 함수가 발생했을 경우
                                    while_conditional_list[callee[8]].append(callee)
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                elif for_first_conditional_start[callee[8]] == 1: #for문 첫번째 조건확인에서 함수 발생
                                    for_first_conditional_list[callee[8]].append(callee)
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                elif for_second_conditional_start[callee[8]] == 1:  #for문 두번째 실행부분에서 함수 발생
                                    for_second_conditional_list[callee[8]].append(callee)
                                    temp_for_second = 1
                                    #for문 두번째는 바로 실행하는 것은 아니기 때문에 연결시켜주면 안된다.
                                elif while_ongoing_start[callee[8]] == 1:   #for문이든 while문이든 조건문 내부가 아니라 반복문 내부에서 함수가 발생했을 경우
                                    if len(while_ongoing_start_list[callee[8]]) < 2:   #비어있으면 즉 while조건문 조건확인 및 내부에서 한번도 함수가 호출되지 않았을 경우
                                        while_ongoing_start_list[callee[8]].append(callee)
                                    if not while_ongoing_end_list[callee[8]]:
                                        while_ongoing_end_list[callee[8]].append(callee)
                                    else:
                                        while_ongoing_end_list[callee[8]].clear()
                                        while_ongoing_end_list[callee[8]].append(callee)
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                elif for_ongoing_start[callee[8]] == 1:
                                    if len(for_ongoing_start_list[callee[8]]) < 2:  #lambda 빼고도 하나 더있는지 확인해야함
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
                                    if len(do_while_ongoing_start_list[callee[10]]) < 2: #lambda개수까지 고려해서 2개까지 저장 이게 나중에 쓰려고 하는게 아니라 working인지 판단만 하려고 저장하는거다.
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
                        else:   #진짜 아무것도 아닐때
                            make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                        if temp_for_second == 0:    #for문 두번째에서는 이전함수 정보에 대한 갱신작업이 일어나면 안된다.
                            prev_callee_list.clear()
                            prev_callee_list.append(callee)
                    #if 삼항연산자 체크
                    else:
                        if control_flow_return_val == IF_CONTROL:
                            if callee[0] == 'start_info':
                                if callee[1] == 'if' or callee[1] == 'conditional':
                                    #control_flow_start_function_list.append([])
                                    #control_flow_end_function_list.append([])
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
                                    prev_callee_list.clear()        #일관성을 위해서
                                    for temp_if_prev_start in if_prev_start_list[callee[2]]:
                                        prev_callee_list.append(temp_if_prev_start)
                                    if_ongoing_start_list[callee[2]].append([])
                                    if_ongoing_end_list[callee[2]].append([])
                                elif callee[1] == 'else if':
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                        if control_flow_can_empty == 0:         #이전것이 비어있을 수가 없다고 함
                                            if_function_in_list[callee[2]] = 1
                                        if not if_ongoing_start_list[callee[2]][-1]:    #이전에 호출된 것이 하나도 없으면 리스트가 비어있으면
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
                                        if if_function_in_list[callee[2]] == 1:
                                            if if_ongoing_end_list[callee[2]]:
                                                if_ongoing_end_list[callee[2]].pop()

                                    if if_function_in_list[callee[2]] == 0:
                                        if_function_in_final_list[callee[2]] = 1
                                    if_function_in_list[callee[2]] = 0
                                    else_if_ongoing_start[callee[2]] = 1


                                                        
                                    prev_callee_list.clear()
                                    for temp_if_prev_start in if_prev_start_list[callee[2]]:
                                        prev_callee_list.append(temp_if_prev_start)
                                    if_ongoing_end_list[callee[2]].append([])
                                    if_ongoing_start_list[callee[2]].append([])
                                    #print('!!!!!!!!!!!!',if_ongoing_start_list[callee[2]])
                                elif callee[1] == 'else':
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                        if control_flow_can_empty == 0:         #이전것이 비어있을 수가 없다고 함
                                            if_function_in_list[callee[2]] = 1
                                        if not if_ongoing_start_list[callee[2]][-1]:    #이전에 호출된 것이 하나도 없으면 리스트가 비어있으면
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
                                        if if_function_in_list[callee[2]] == 1:
                                            if if_ongoing_end_list[callee[2]]:
                                                if_ongoing_end_list[callee[2]].pop()

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
                            else:   #end_info
                                #print('before',prev_callee_list)
                                if control_flow_end == 1:
                                    control_flow_end = 0
                                    if control_flow_can_empty == 0:         #이전것이 비어있을 수가 없다고 함
                                        if_function_in_list[callee[2]] = 1
                                    #if_function_in_list[callee[2]] = 1  #else에서 따로 함수가 호출된건 아니지만 control flow가 발생해서 그안에서 함수가 출된 경우
                                    if not if_ongoing_start_list[callee[2]][-1]:    #이전에 호출된 것이 하나도 없으면 리스트가 비어있으면
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
                                    if if_function_in_list[callee[2]] == 1:
                                        if if_ongoing_end_list[callee[2]]:
                                            if_ongoing_end_list[callee[2]].pop()

                                temp_control_flow_can_empty_check = 0
                                if else_ongoing_start[callee[2]] == 0:
                                    temp_control_flow_can_empty_check = 1
                                else:
                                    if if_function_in_list[callee[2]] != 1 or if_function_in_final_list[callee[2]] != 0:
                                        temp_control_flow_can_empty_check = 1
                                if temp_control_flow_can_empty_check == 0:
                                    control_flow_can_empty = 0

                                
                                prev_callee_list.clear()
                                if if_ongoing_end_list[callee[2]]: #if문 끝났는데 else if가 남아있다면
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
                                    else:           #else가 있고 모든 if, else if, else 구문에 함수가 호출됐었다. 그런데도 비었으면 이것은 리턴이 다 있었다는 의미
                                        if not if_ongoing_end_list[callee[2]]:
                                            control_flow_skip = 1
                                            control_flow_skip_list.append((RETURN_CONTROL,0))   #끝까지 스킵
                                            for temp_if_return_cul_list in if_return_cul_list[callee[2]]:
                                                for temp_if_return_cul in temp_if_return_cul_list:
                                                        if function_not_in_list(temp_if_return_cul,prev_callee_list) == 0:
                                                            prev_callee_list.append(temp_if_return_cul)
                                            if len(control_flow_list) > 1:
                                                print_error_for_make_function('why return return return?')

                                        

                                #print('after',prev_callee_list)
                            
                                
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
                                    control_flow_end_list.append(control_flow_end)  #직전 상태정보 저장
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
                                    switch_function_in_list[callee[2]] = 1 #제일 처음 switch가 나오고나서 다음 case까지는 함수가 없을 수 있음
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
                                                    #switch_ongoing_start_list[callee[2]].append(temp_prev_callee)
                                                    switch_ongoing_end_list[callee[2]][-1].append(temp_prev_callee)
                                        else:
                                            print_error_for_make_function('control_flow in switch - case?')

                                    if switch_return_list[callee[2]] and switch_ongoing_break_if[callee[2]] == 1:
                                        print_error_for_make_function('break, return in case?')

                                    #return 관련처리
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
                                    #여기가 새로 추가되는 부분 debugdebugdebug!!
                                    if switch_ongoing_break[callee[2]] == 0:
                                        if switch_ongoing_end_list[callee[2]]:
                                            if switch_ongoing_end_list[callee[2]][-1]:
                                                for temp_switch_ongoing_end in switch_ongoing_end_list[callee[2]][-1]:
                                                    if function_not_in_list(temp_switch_ongoing_end,prev_callee_list) == 0:
                                                        prev_callee_list.append(temp_switch_ongoing_end)
                                        """
                                        for temp_index in range(len(switch_ongoing_end_list[callee[2]]) - 1, -1, -1):
                                            if switch_ongoing_end_list[callee[2]][temp_index]:
                                                for temp_switch_ongoing_end in switch_ongoing_end_list[callee[2]][temp_index]:
                                                    if function_not_in_list(temp_switch_ongoing_end,prev_callee_list) == 0:
                                                        prev_callee_list.append(temp_switch_ongoing_end)
                                                break
                                        """
                                    #끝
                                    if switch_ongoing_break_if[callee[2]] == 1:
                                        switch_ongoing_end_list[callee[2]].append([])   #break있을때만 []를 추가 즉 없으면 그대로 사용함을 알 수 있다.
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
                                                    #switch_ongoing_start_list[callee[2]].append(temp_prev_callee)
                                                    switch_ongoing_end_list[callee[2]][-1].append(temp_prev_callee)
                                        else:
                                            print_error_for_make_function('control_flow in switch - case?')

                                    #return 관련처리
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
                                #print('before',prev_callee_list)
                                if control_flow_end == 1:
                                    control_flow_end = 0
                                    if control_flow_can_empty == 0:
                                        switch_function_in_list[callee[2]] = 1
                                    switch_ongoing_end_list[callee[2]][-1].clear()
                                    for temp_prev_callee in prev_callee_list:
                                        if temp_prev_callee[7] >= control_flow_information_list[-1][3] and temp_prev_callee[7] <= callee[3]:
                                            switch_working_list[callee[2]] = 1
                                            switch_ongoing_end_list[callee[2]][-1].append(temp_prev_callee)
                                            #switch_ongoing_start_list[callee[2]].append(temp_prev_callee)

                                #return 관련처리
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
                                if switch_ongoing_end_list[callee[2]]: #if문 끝났는데 else if가 남아있다면
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
                                            control_flow_skip = 1
                                            control_flow_skip_list.append((RETURN_CONTROL,0))
                                            for temp_switch_return_cul_list in switch_return_cul_list[callee[2]]:
                                                for temp_switch_return_cul in temp_switch_return_cul_list:
                                                        if function_not_in_list(temp_switch_return_cul,prev_callee_list) == 0:
                                                            prev_callee_list.append(temp_switch_return_cul)
                                            if len(control_flow_list) > 1:
                                                print_error_for_make_function("why return return return? (switch)")
                                        

                                if switch_break_list[callee[2]]:
                                    for temp_switch_break_list in switch_break_list[callee[2]]:
                                        for temp_switch_break in temp_switch_break_list:
                                            if function_not_in_list(temp_switch_break,prev_callee_list) == 0:
                                                prev_callee_list.append(temp_switch_break)


                                #print('after',prev_callee_list)
                            
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
                                    control_flow_end_list.append(control_flow_end)  #직전의 control_flow_end 상태를 저장한다.
                                    control_flow_end = 0    #이전에 control_flow_end가 있었어도 일단은 안에서 발생한 것이 아니므로 0으로 바꾼다.
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
                                        while_ongoing_start_list[callee[2]].append(while_conditional_list[callee[2]][0])   #가장 처음것만 넣어준다.
                                        if len(while_conditional_list[callee[2]]) > 1:
                                            while_ongoing_start_list[callee[2]].append(while_conditional_list[callee[2]][1])    #처음은 람다니까 다음것도 넣어준다.
                                        while_ongoing_end_list[callee[2]].append(while_conditional_list[callee[2]][-1])
                                        #while문은 가장 마지막과 가장 처음을 연결시켜줘야하기 때문
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
                                    #print(while_ongoing_start_list[callee[2]],while_ongoing_end_list[callee[2]],prev_callee_list)
                                    if control_flow_end == 1:
                                        control_flow_end = 0
                                        if prev_callee_list:
                                            while_ongoing_end_list[callee[2]].clear() #while문이 끝나기전에 if문 같은것이 끝나고 함수가 따로 호출된적이 없을 경우에는 if문에서 발생한 함수로 마지막을 채워줘야한다.
                                            for temp_prev_callee in prev_callee_list:
                                                if temp_prev_callee[9] >= control_flow_information_list[-1][3] and temp_prev_callee[9] <= callee[3]:  #같은 while문 내에서 발생한 것에 대해서만
                                                    while_working_list[callee[2]] = 1
                                                    while_ongoing_end_list[callee[2]].append(temp_prev_callee)
                                            if len(while_ongoing_start_list[callee[2]]) < 2:     #while문 내부에서 따로 함수호출이 없었을 경우 처음은 람다임
                                                for temp_prev_callee in prev_callee_list:
                                                    if temp_prev_callee[9] >= control_flow_information_list[-1][3] and temp_prev_callee[9] <= callee[3]:  #같은 while문 내에서 발생한 것에 대해서만
                                                        if while_ongoing_start_list[callee[2]][0] != temp_prev_callee:
                                                            while_ongoing_start_list[callee[2]].append(temp_prev_callee)
                                                        #if while_ongoing_start_list[callee[2]][0] != temp_prev_callee:
                                    #print(while_ongoing_start_list[callee[2]],while_ongoing_end_list[callee[2]],prev_callee_list)
                                    if while_ongoing_start_list[callee[2]] and while_ongoing_end_list[callee[2]]:
                                        make_connect(call_graph_matrix,call_graph_function_pos,caller,while_ongoing_end_list[callee[2]],while_ongoing_start_list[callee[2]][0])
                                        #for temp_while_ongoing_start in while_ongoing_start_list[callee[2]]:
                                        #    make_connect(call_graph_matrix,call_graph_function_pos,caller,while_ongoing_end_list[callee[2]],temp_while_ongoing_start)
                                    elif not while_ongoing_start_list[callee[2]] and not while_ongoing_end_list[callee[2]]:
                                        if while_working_list[callee[2]] == 1:
                                            print_error_for_make_function('while control working error2')
                                    else:
                                        print(while_ongoing_start_list[callee[2]],while_ongoing_end_list[callee[2]],while_working_list[callee[2]])
                                        print_error_for_make_function('while control working error3')

                                    if iteration_continue_list[callee[2]]:  #continue가 있으면 가장 처음이랑 연결시켜준다.
                                        for temp_iteration_continue in iteration_continue_list[callee[2]]:
                                            make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_iteration_continue,while_ongoing_start_list[callee[2]][0])
                                    #lambda 함수와 관련된 연결을 원래상태로 되돌린다.
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
                                #print('!!!',iteration_lambda_function_name,input_to_iteration_lambda_list,output_to_iteration_lambda_list)
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
                                    #elif not while_conditional_list[callee[2]] and not while_ongoing_start_list[callee[2]]:
                                    #    print(while_ongoing_end_list[callee[2]])
                                    #    print_error_for_make_function('while control working error4')

                                    if while_conditional_list[callee[2]]:
                                        control_flow_can_empty = 0
                                    #다음 단계로 넘겨 줄 때 prev_callee_list를 채우는 로직
                                    prev_callee_list.clear()
                                    if while_conditional_list[callee[2]]: #while문 조건문쪽에서 함수가 있을 경우
                                        prev_callee_list.clear()
                                        if function_not_in_list(while_conditional_list[callee[2]][-1],prev_callee_list) == 0:
                                            prev_callee_list.append(while_conditional_list[callee[2]][-1])    
                                    if while_ongoing_end_list[callee[2]] and not while_conditional_list[callee[2]]: #조건문안에 함수가 없어야 while문 끝나고 다음으로 연결될 수 있다.
                                        for temp_while_ongoing_end in while_ongoing_end_list[callee[2]]:
                                            if function_not_in_list(temp_while_ongoing_end,prev_callee_list) == 0:
                                                prev_callee_list.append(temp_while_ongoing_end)
                                    if while_prev_start_list[callee[2]] and not while_conditional_list[callee[2]]: #조건문안에는 함수가 없어야 while문전에 prev가 다음단계와 연결될 수 있다.
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
                                            #temp_for_ongoing_end_list = for_ongoing_end_list[callee[2]].copy()
                                            for_ongoing_end_list[callee[2]].clear() #while문이 끝나기전에 if문 같은것이 끝나고 함수가 따로 호출된적이 없을 경우에는 if문에서 발생한 함수로 마지막을 채워줘야한다.
                                            for temp_prev_callee in prev_callee_list:
                                                if temp_prev_callee[9] >= control_flow_information_list[-1][3] and temp_prev_callee[9] <= callee[3]:
                                                    while_working_list[callee[2]] = 1
                                                    for_ongoing_end_list[callee[2]].append(temp_prev_callee)
                                            #if not for_ongoing_end_list[callee[2]]:
                                            #    for_ongoing_end_list[callee[2]] = temp_for_ongoing_end_list
                                            if len(for_ongoing_start_list[callee[2]]) < 2:     #while문 내부에서 따로 함수호출이 없었을 경우
                                                for temp_prev_callee in prev_callee_list:
                                                    if temp_prev_callee[9] >= control_flow_information_list[-1][3] and temp_prev_callee[9] <= callee[3]:
                                                        if for_ongoing_start_list[callee[2]][0] != temp_prev_callee:
                                                            for_ongoing_start_list[callee[2]].append(temp_prev_callee)
                                    """
                                    if not for_ongoing_start_list[callee[2]] and not for_ongoing_end_list[callee[2]]:

                                    elif for_ongoing_start_list[callee[2]] and for_ongoing_end_list[callee[2]]:

                                    else:
                                        print_error_for_make_function('for_ongoing_start_list error1')
                                    """

                                    if iteration_continue_list[callee[2]]:  #continue가 있으면 가장 처음이랑 연결시켜준다.
                                        if for_second_conditional_list[callee[2]]:
                                            for temp_iteration_continue in iteration_continue_list[callee[2]]:
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_iteration_continue,for_second_conditional_list[callee[2]][0])
                                        elif for_first_conditional_list[callee[2]]:
                                            for temp_iteration_continue in iteration_continue_list[callee[2]]:
                                                make_connect(call_graph_matrix,call_graph_function_pos,caller,temp_iteration_continue,for_first_conditional_list[callee[2]][0])
                                            
                                    prev_callee_list.clear()
                                    if for_first_conditional_list[callee[2]] and for_second_conditional_list[callee[2]]:
                                        if len(for_second_conditional_list[callee[2]]) > 1: #2개이상인경우에만
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
                                        if len(for_second_conditional_list[callee[2]]) > 1: #2개이상인경우에만
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
                                            #print(callee)
                                            #print(for_ongoing_start_list[callee[2]],for_ongoing_end_list[callee[2]])
                                            print_error_for_make_function('for_first & for_second error4')
                                        else:
                                            if while_working_list[callee[2]] == 1:
                                                print_error_for_make_function('for_first & for_second error5')
                                    
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
                                    #print('!!!',iteration_lambda_function_name,input_to_iteration_lambda_list,output_to_iteration_lambda_list)
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

                                    #다음으로 넘겨줄 prev_callee_list 설정
                                    if for_first_conditional_list[callee[2]]:
                                        control_flow_can_empty = 0
                                        
                                    prev_callee_list.clear()
                                    if for_first_conditional_list[callee[2]] and for_second_conditional_list[callee[2]]:
                                        if for_ongoing_end_list[callee[2]] and for_ongoing_start_list[callee[2]]:
                                            prev_callee_list.append(for_first_conditional_list[callee[2]][-1]) #for이 끝난후에는 반드시 첫번쨰 조건문 마지막이 다음으로 연결될 수 있다.
                                        else:
                                            print_error_for_make_function('for_first & for_second error6')
                                    elif for_first_conditional_list[callee[2]] and not for_second_conditional_list[callee[2]]:
                                        if for_ongoing_end_list[callee[2]] and for_ongoing_start_list[callee[2]]:
                                            prev_callee_list.append(for_first_conditional_list[callee[2]][-1]) #for이 끝난후에는 반드시 첫번쨰 조건문 마지막이 다음으로 연결될 수 있다.
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
                                                #print(temp_prev_callee,control_flow_information_list,callee)
                                                if temp_prev_callee[11] >= control_flow_information_list[-1][3] and temp_prev_callee[11] <= callee[3]:  #같은 do_while문 내에서 발생한 것에 대해서만
                                                    do_while_working_list[callee[2]] = 1
                                                    do_while_ongoing_end_list[callee[2]].append(temp_prev_callee)
                                            if not do_while_ongoing_start_list[callee[2]]:
                                                for temp_prev_callee in prev_callee_list:
                                                    if temp_prev_callee[11] >= control_flow_information_list[-1][3] and temp_prev_callee[11] <= callee[3]:  #같은 do_while문 내에서 발생한 것에 대해서만
                                                        do_while_ongoing_start_list[callee[2]].append(temp_prev_callee)
                                        #print(do_while_ongoing_end_list[callee[2]],prev_callee_list,'!!!!!!!!!')
                                        #print(do_while_ongoing_start_list[callee[2]],do_while_ongoing_end_list[callee[2]])

                                    #do_while conditional 부분에서는 control_flow가 나타날 가능성이 없으므로 0으로 초기화 하면 안될 수도 있다.
                                else:
                                    print_error_for_make_function('do_while control error')
                            else:
                                if callee[1] == 'do_while':
                                    if control_flow_end == 1:
                                        print_error_for_make_function('control_flow_end == 1?')

                                    if do_while_continue_list[callee[2]]:  #continue가 있으면 가장 처음이랑 연결시켜준다.
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
                                    



                                    if do_while_function_call_normal_list[callee[2]] == 1: #조건 속에 함수가 있으면 무조건 안 지나갈 수가 없다.
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
                                        if temp_index == len(control_flow_list) - 1:
                                            do_while_ongoing_break[control_flow_information_list[temp_index][2]] = 1
                                            control_flow_skip_list.append((DO_WHILE_CONTROL,control_flow_information_list[temp_index],'break'))
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
                                control_flow_skip_list.append((RETURN_CONTROL,0))   #끝까지 스킵
                                control_flow_skip = 1
                                #control flow 내부가 아니라 그냥 함수 종료를 알리는 경우임

                                        
                                    
                    #for while 체크
                        
                                        #do while문 체크

                    #switch 체크

                    #break문 체크

                    #continue문 체크

                    #일반적인상황
                """
                print(caller,'debug')
                print(prev_callee_list)
                for item in if_prev_start_list:
                    if item:
                        print('if_prev_start_list',item)
                for item in if_ongoing_start_list:
                    if item:
                        print('if_ongoing_start_list',item)
                for item in if_ongoing_start_level_list:
                    if item:
                        print('if_ongoing_start_level_list',item)        
                """
                if FOR_DEVELOPMENT == 1:
                    print_matrix(call_graph_matrix,call_graph_function_pos,caller)
                #print(call_graph_matrix[caller])
                for temp_lambda_function_name in control_flow_iteration_lambda_function:
                    del call_graph_matrix[caller][temp_lambda_function_name]
                    del call_graph_function_pos[caller][temp_lambda_function_name]
                for temp_function_name, temp_graph_list in call_graph_matrix[caller].items():
                #    print(len(temp_graph_list))
                    del temp_graph_list[:100]
                #    print(len(temp_graph_list))
                #print(call_graph_matrix)
                if while_check == 1:
                    call_graph_matrix_list.append(call_graph_matrix[caller].copy())
                    call_graph_function_pos_list.append(call_graph_function_pos[caller].copy())
                    caller_list.append(caller)
                #print('hello')
                #print(call_graph_matrix[caller]['S'][call_graph_function_pos[caller]['E']-100],call_graph_function_pos[caller])
                #print('hi')
                """
                for function_name, function_call_matrix in call_graph_matrix[caller].items():
                    temp_connect_list = []
                    for temp_index in range(0,len(function_call_matrix)):
                        temp_function_pos = {value: key for key, value in call_graph_function_pos[caller].items()}
                        if function_call_matrix[temp_index] == 1:
                            temp_connect_list.append(temp_function_pos[temp_index])
                    print(function_name,temp_connect_list)
                """
                if while_check == 0:
                    if call_graph_matrix[caller]['S'][call_graph_function_pos[caller]['E']-100] == 1:
                        not_call_function_set.add(caller)
        if while_check == 1:
            break
            
        if while_check == 0 and not_call_function_set == prev_not_call_function_set:
            while_check = 1
            not_call_function_set.clear()
        elif while_check == 0:
            prev_not_call_function_set = not_call_function_set.copy()
    #print(prev_not_call_function_set,'not call!!!')
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
            print(f"{caller} -> (끝)")
    print("Function Call Graph Lengh: "+str(call_length))


def check_SE_direct_by_structure(global_call_graph):
    result = {}

    def has_SE_direct_edge(func_name, visited):
        if func_name in visited:
            return False
        visited.add(func_name)

        if func_name not in global_call_graph:
            return False

        start_set, end_set, call_map = global_call_graph[func_name]

        for start_func in start_set:
            # 1. 이 함수가 사용자 정의 함수이면 그 내부 구조를 재귀적으로 검사
            if start_func in global_call_graph:
                if has_SE_direct_edge(start_func, visited.copy()):
                    return True

            # 2. 일반 함수일 경우 call_map에서 E로 가는 엣지가 있는지 확인
            #if start_func in call_map and 'E' in call_map[start_func]:
            #    return True

        return False

    for func in global_call_graph:
        result[func] = has_SE_direct_edge(func, set())

    return result





def merge_all_graphs(call_graph_matrix_list,call_graph_function_pos_list,caller_list,user_functions,all_functions,not_call_function_set):

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

    #print(user_functions_list)
    #print(all_functions)

    #res = check_SE_direct_by_structure(call_graph_matrix_use_name)
    #print(res)    
    """
    #E_function_set = set([])
    #not_E_function_set = set([])
    #ongoing_E_function_set = set([])
    #for function_name, temp_call_graph_matrix_use_name in call_graph_matrix_use_name.items():
    #    ongoing_E_function_set.add(function_name)
    #for function_name, temp_call_graph_matrix_use_name in call_graph_matrix_use_name.items():
        if 'E' in temp_call_graph_matrix_use_name[0]:
            E_function_set.add(function_name)
            ongoing_E_function_set.discard(function_name)
            continue
        else:
            for func in temp_call_graph_matrix_use_name[0]:
                if func in user_functions_list:
                    if 'E' in call_graph_matrix_use_name[func][0]:
                        E_function_set.add(function_name)
                        ongoing_E_function_set.discard(function_name)
                        continue 
    """



    
    
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
                    if func in not_call_function_set:  #풀어줄 함수가 E가 있는 함수면
                        temp_start_set = temp_start_set.union(call_graph_matrix_use_name[function_name][2][func])
                        temp_start_set = temp_start_set.union(call_graph_matrix_use_name[func][0])
                    else:
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
                    if func in not_call_function_set:  #풀어줄 함수가 S가 있는 함수면
                        temp_s_set = set([])
                        #여기 다시 봐야함
                        for temp_function_name, temp_call_graph in call_graph_matrix_use_name[function_name][2].items():
                            if func in temp_call_graph:
                                temp_s_set.add(temp_function_name)
                        #if 'S' not in call_graph_matrix_use_name_copy[function_name][1] and 'E' not in call_graph_matrix_use_name[function_name][2][func]:
                        temp_end_set = temp_end_set.union(temp_s_set)
                        temp_end_set = temp_end_set.union(call_graph_matrix_use_name[func][1])
                    else:
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
                        if func in not_call_function_set:  #풀어줄 함수가 E가 있는 함수면
                            temp_set = temp_set.union(call_graph_matrix_use_name[function_name][2][func])
                            temp_set = temp_set.union(call_graph_matrix_use_name[func][0])
                        else:
                            temp_set = temp_set.union(call_graph_matrix_use_name[func][0])
                        visited_dict[function_name].add(func)
                    else:
                        temp_set.add(func)
                    temp_set.discard('E')
                if call_graph_matrix_use_name_copy[function_name][2][src] == temp_set:
                    break
                call_graph_matrix_use_name_copy[function_name][2][src] = temp_set.copy()

    #for function_name, temp_call_graph_matrix_use_name in call_graph_matrix_use_name.items():
    #    print('before')
    #    print(function_name,call_graph_matrix_use_name[function_name][0],call_graph_matrix_use_name[function_name][1],call_graph_matrix_use_name[function_name][2])
    #    print('after')
    #    print(function_name,call_graph_matrix_use_name_copy[function_name][0],call_graph_matrix_use_name_copy[function_name][1],call_graph_matrix_use_name_copy[function_name][2])
    

    
    #for function_name, temp_call_graph_matrix_use_name in call_graph_matrix_use_name.items():
    #    print('before')
    #    print(function_name,call_graph_matrix_use_name[function_name][0],call_graph_matrix_use_name[function_name][1])
    #    print('after')
    #    print(function_name,call_graph_matrix_use_name_copy[function_name][0],call_graph_matrix_use_name_copy[function_name][1])
    
    #각 함수의 진입점, 종착점 사용자 함수를 전부 libc함수로 변경완료

    #print(call_graph_matrix)
    
    call_functions = all_functions - user_functions
    user_functions_list = list(user_functions)
    call_functions_list = list(call_functions)
    visited_list = [0] * len(user_functions)
    visited_list_name = user_functions_list.copy()
    user_functions_start = []
    user_functions_end = []
    merged_call_graph_matrix = []
    merged_call_graph_matrix_name = []
    merged_call_graph_matrix_pos = {}          #이름으로 번호
    merged_call_graph_matrix_pos_revert = {}   #번호로 이름
    for temp_all_function_name in call_functions_list:
        merged_call_graph_matrix.append([0] * len(call_functions_list))
        merged_call_graph_matrix_name.append(temp_all_function_name)
    for temp_index in range(0,len(merged_call_graph_matrix_name)):
        merged_call_graph_matrix_pos[merged_call_graph_matrix_name[temp_index]] = temp_index
        merged_call_graph_matrix_pos_revert[temp_index] = merged_call_graph_matrix_name[temp_index]
    for function_name, temp_call_graph_matrix_use_name_copy in call_graph_matrix_use_name_copy.items():
        #print(function_name,temp_call_graph_matrix_use_name_copy)
        for src, dst in temp_call_graph_matrix_use_name_copy[2].items():
            if src in user_functions_list:
                for temp_src in call_graph_matrix_use_name_copy[src][1]:
                    for temp_dst in dst:
                        merged_call_graph_matrix[merged_call_graph_matrix_pos[temp_src]][merged_call_graph_matrix_pos[temp_dst]] = 1
            else:
                for temp_dst in dst:
                    merged_call_graph_matrix[merged_call_graph_matrix_pos[src]][merged_call_graph_matrix_pos[temp_dst]] = 1
    #for temp_index in range(0,len(merged_call_graph_matrix_name)):
    #    print(merged_call_graph_matrix_name[temp_index],merged_call_graph_matrix[temp_index])
    make_graph_using_gui_use_list(merged_call_graph_matrix_name,merged_call_graph_matrix)
    #print(merged_call_graph_matrix,merged_call_graph_matrix_name)
    #print(merged_call_graph_matrix_pos)
    #print(len(merged_call_graph_matrix_pos),len(merged_call_graph_matrix),len(merged_call_graph_matrix_name))
    #print(visited_list,visited_list_name)
    
    
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="C file analyzer with optional graph generation.")
    parser.add_argument("c_file", help="Path to the C source file")
    parser.add_argument("-g", "--graph", action="store_true", help="Generate graph from adjacency matrix")
    parser.add_argument("-m", "--merge", action="store_true", help="Merge all function graphs into a single graph")


    args = parser.parse_args()

    c_file = args.c_file
    make_graph = args.graph
    merge_graph = args.merge 

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
        merge_all_graphs(call_graph_matrix_list, call_graph_function_pos_list, caller_list, user_functions, all_functions, not_call_function_set)

