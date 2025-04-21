import subprocess
import re
import sys
import os

#control constant
NORMAL_CONTROL = 0
IF_CONTROL = 0b1
SWITCH_CONTROL = 0b10
WHILE_CONTROL = 0b100
DO_WHILE_CONTROL = 0b1000
BREAK_CONTROL = 0b10000
CONTINUE_CONTROL = 0b100000



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

def get_first_alpha_index(line):
    """Find the index of the first alphabetic character (A-Z, a-z) in a given line."""
    match = re.search(r"[a-zA-Z]", line)
    return match.start() if match else float('inf')  # Return a large value if no alphabet found

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
                call_depth = get_first_alpha_index(line)  # 알파벳이 처음 등장하는 위치로 깊이 설정
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
                call_depth = get_first_alpha_index(line)  # 알파벳이 처음 등장하는 위치로 깊이 설정
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
        for_depth = 0
        for_ongoing = 0
        for_ongoing_depth = []
        for_first_ongoing = 0

        do_while_new_check = 0
        do_while_level = 0
        do_while_ongoing_depth = []
        do_while_first_ongoing = 0

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
        

        for i in range(len(lines)):
            line = lines[i]
            current_depth = get_first_alpha_index(line)

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
                            function_calls[current_function].append(('end_info','if',if_level))
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
                            function_calls[current_function].append(('end_info','conditional',if_level))
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
                            function_calls[current_function].append(('end_info','while',while_level))
                            while_level -= 1
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
                            function_calls[current_function].append(('end_info','for',while_level))
                            while_level -= 1
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
                            function_calls[current_function].append(('end_info','for',do_while_level))
                            do_while_level -= 1
                            do_while_ongoing_depth.pop()   
                            control_flow_re_check = 1
                            control_flow_list.pop()
                            if not do_while_ongoing_depth:
                                break    
                elif temp_control_flow == SWITCH_CONTROL:
                    if switch_ongoing_depth:
                        while current_depth <= switch_ongoing_depth[-1]:
                            if switch_level <= 0:
                                print_error('switch level error',lines,i)
                            function_calls[current_function].append(('end_info','switch',switch_level))
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

            if "WhileStmt" in line:
                while_first_ongoing = 1
                temp_while_current_depth = get_first_alpha_index(line)
                while_ongoing_depth.append(temp_while_current_depth)
            
            if while_first_ongoing > 0:
                if while_first_ongoing == 2:
                    while_first_ongoing = 0
                    if_conditional_depth = get_first_alpha_index(line)
                    while_depth_list.append(if_conditional_depth)
                    while_ongoing = 1
                else:
                    while_first_ongoing = 2


            if while_ongoing > 0:
                if while_ongoing == 2:
                    temp_current_depth = get_first_backtick_index(line)
                    if temp_current_depth + 2 == while_depth:
                        while_level += 1
                        function_calls[current_function].append(('start_info','while',while_level))
                        control_flow_list.append(WHILE_CONTROL)
                        if while_new_check >= 1000: #실제로 반복문 내부로 들어왔을 때만 증가하게
                            while_new_check = 0
                        while_new_check += 1
                        while_ongoing = 0
                else:
                    while_ongoing = 2   
            #for문 시작

            if for_depth_list:          
                for_depth = for_depth_list[-1]

            if "ForStmt" in line:
                for_first_ongoing = 1
                temp_for_current_depth = get_first_alpha_index(line)
                for_ongoing_depth.append(temp_for_current_depth)
            
            if for_first_ongoing > 0:
                if for_first_ongoing == 2:
                    for_first_ongoing = 0
                    temp_for_current_depth = get_first_alpha_index(line)
                    for_depth_list.append(temp_for_current_depth)
                    for_ongoing = 1
                else:
                    for_first_ongoing = 2


            if for_ongoing > 0:
                if for_ongoing == 2:
                    temp_current_depth = get_first_backtick_index(line)
                    if temp_current_depth + 2 == for_depth:
                        while_level += 1
                        function_calls[current_function].append(('start_info','for',while_level))
                        control_flow_list.append(WHILE_CONTROL)
                        if while_new_check >= 1000:
                            while_new_check = 0
                        while_new_check += 1
                        for_ongoing = 0
                else:
                    for_ongoing = 2   
            
            #do-while문 시작

            if "DoStmt" in line:
                do_while_first_ongoing = 1
                temp_do_while_current_depth = get_first_alpha_index(line)
                do_while_ongoing_depth.append(temp_do_while_current_depth)
            
            if do_while_first_ongoing > 0:
                if do_while_first_ongoing == 2:
                    do_while_first_ongoing = 0
                    temp_do_while_current_depth = get_first_alpha_index(line)
                    do_while_level += 1
                    function_calls[current_function].append(('start_info','do_while',do_while_level))
                    control_flow_list.append(DO_WHILE_CONTROL)
                    if do_while_new_check >= 1000:
                        do_while_new_check = 0
                    do_while_new_check += 1
                else:
                    do_while_first_ongoing = 2


            #반복문 끝


            #조건문 진입 시작

            if "ConditionalOperator" in line:
                conditional_operator_first_ongoing = 1
                temp_conditional_current_depth = get_first_alpha_index(line)
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
                    first_conditional_operator_depth = get_first_alpha_index(line)
                    first_conditional_operator_depth_list.append(first_conditional_operator_depth)
                    conditional_operator_ongoing = 1
                else:
                    conditional_operator_first_ongoing = 2

            if conditional_operator_ongoing > 0:
                if conditional_operator_ongoing == 2:
                    if current_depth == first_conditional_operator_depth:
                        if conditional_if_level_plus == 0:
                            if_level += 1
                            function_calls[current_function].append(('start_info','conditional',if_level))
                            control_flow_list.append(IF_CONTROL)
                            conditional_if_level_plus = 1
                        conditional_operator_ongoing = 3
                        conditional_operator_ongoing_list[-1] = 3

                elif conditional_operator_ongoing == 3:
                    if current_depth == first_conditional_operator_depth:
                        if if_level_else_if_list and len(if_level_else_if_list) > if_level: #1번째가 실제로 if_level 1단계이다.
                            if_level_else_if_list[if_level] += 1
                            function_calls[current_function].append(('start_info','else if',if_level))
                        conditional_operator_ongoing = 0
                else:
                    conditional_operator_ongoing = 2
                    conditional_operator_ongoing_list.append(conditional_operator_ongoing)


            #일반 조건문
            if if_conditional_depth_list:
                if_conditional_depth = if_conditional_depth_list[-1]    #마지막 가져오기

            #if has_else_list[if_level] == 1:
                #print(has_else_list)
            #    has_else = has_else_list[if_level][-1]
            if has_else_list[if_level] == 1:
                temp_if_end_depth = get_first_backtick_index(line)
                if temp_if_end_depth + 2 == if_conditional_depth:
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
                        function_calls[current_function].append(('start_info','else if',if_level))
                        #print('if_level else if',if_level)
                        #print('else if has_else',has_else_list)
                        #print(function_calls[current_function])
                        #print_for_debug(lines,i,20)
                    else:
                        function_calls[current_function].append(('start_info','else',if_level))
                        #print('if_level else',if_level)
                        #print('else has_else',has_else_list)
                        #print(function_calls[current_function])
                        #print_for_debug(lines,i,20)
                    has_else_list[if_level] = 0
                    #if_conditional_depth_list.pop()
                    #if_level_else_if_list_not_append = 1
                    if_level_else_if_list_not_append_list[if_level] = 1

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
                temp_if_current_depth = get_first_alpha_index(line)
                if_ongoing_depth.append(temp_if_current_depth)
            
            if if_first_ongoing > 0:
                if if_first_ongoing == 2:
                    if_first_ongoing = 0
                    temp_if_conditional_depth = get_first_alpha_index(line)
                    if if_level_not_plus_list[if_level] == 1:
                        if_conditional_depth_list.pop()
                    if_conditional_depth_list.append(temp_if_conditional_depth)
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
                            function_calls[current_function].append(('start_info','if',if_level))
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

            if "SwitchStmt" in line:
                if switch_not_append == 0:
                    switch_case_list.append(0)
                else:
                    switch_not_append = 0
                if switch_new_check >= 1000:
                    switch_new_check = 0
                switch_new_check += 1
                switch_first_ongoing = 1
                temp_switch_current_depth = get_first_alpha_index(line)
                switch_ongoing_depth.append(temp_switch_current_depth)
            
            if switch_first_ongoing > 0:
                if switch_first_ongoing == 2:
                    switch_first_ongoing = 0
                    temp_switch_current_depth = get_first_alpha_index(line)
                    switch_depth_list.append(temp_switch_current_depth)
                    switch_ongoing = 1
                else:
                    switch_first_ongoing = 2

            if switch_ongoing > 0:
                if switch_ongoing == 2:
                    temp_current_depth = get_first_backtick_index(line)
                    if temp_current_depth == switch_depth:
                        switch_level += 1
                        function_calls[current_function].append(('start_info','switch',switch_level))
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
                        function_calls[current_function].append(('start_info','case',switch_level))
                    if "DefaultStmt" in line:
                        function_calls[current_function].append(('start_info','default',switch_level))
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
                temp_function_call_depth = get_first_alpha_index(line)
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
        return function_calls
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
            current_depth = get_first_alpha_index(line)
            
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
                function_call_depth = get_first_alpha_index(line)
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
                call_depth = get_first_alpha_index(line)
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
    if callee[1] == 'for' or callee[1] =='while':
        #print('while!!')
        control_return_val |= WHILE_CONTROL
    if callee[1] == 'do_while':
        #print('do while!!')
        control_return_val |= DO_WHILE_CONTROL
    if callee[1] == 'break':
        #print('break!!')
        control_return_val |= BREAK_CONTROL
    if callee[1] == 'continue':
        #print('continue!!')
        control_return_val |= CONTINUE_CONTROL
    if control_return_val == NORMAL_CONTROL:
        print_error_for_make_function('control flow error')
    return control_return_val

def make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee):
    if not prev_callee_list:
        print_error_for_make_function('make connect error')
    for temp_prev_callee in prev_callee_list:
        call_graph_matrix[caller][temp_prev_callee[-1]][call_graph_function_pos[caller][callee[-1]]] = 1

def function_not_in_list(callee,callee_list):
    check = 0
    for temp_callee in callee_list:
        if callee[-1] == temp_callee[-1]:
            check = 1
            break
    return check


def make_matrix_from_function_graph(function_graph):
    call_graph_matrix = {}
    call_graph_function_pos = {}
    call_graph_function_start  = {}
    call_graph_function_end = {}
    for caller, callees in function_graph.items():
        call_graph_matrix[caller] = {}
        call_graph_function_pos[caller] = {}
        call_graph_function_start[caller] = []
        call_graph_function_end[caller] = []
        if callees:
            print(caller)
            function_set = set([])
            function_set.add('start_callee!')
            for callee in callees:
                if control_flow_check(callee) == NORMAL_CONTROL:
                    function_set.add(callee[-1])
            index = 0
            for function_name in function_set:
                call_graph_matrix[caller][function_name] = [0]*len(function_set)
                call_graph_function_pos[caller][function_name] = index
                index += 1
            #for function_name, function_call_matrix in call_graph_matrix[caller].items():
            #    print(function_name,function_call_matrix)
            #print(call_graph_function_pos[caller])
            #기본 matrix 및 함수당 인덱스 매핑 완료

            prev_callee = (0,0,0,0,0,0,0,0,0,0,0,0,0,0,'start_callee!')
            prev_callee_list = []
            prev_callee_list.append(prev_callee)
            start_make_matrix = 0
            for_prev_start = [[] for _ in range(100)] #for, while문 시작직전 함수 담는 스택
            for_after_start = [[] for _ in range(100)] #for, while문 시작직후 함수 담는 스택
            do_after_start = [[] for _ in range(100)] #do while문 시작직후 함수 담는 스택 (직전은 필요없음)
           
            
            control_flow_list = []  #control flow 정보를 담는 list
            control_flow = 0
            control_flow_end = 0
            control_flow_start_function_list = [] #control flow가 끝나고 start정보를 담는 리스트
            control_flow_end_function_list = [] #control flow가 끝나고 end정보를 담는 리스트

            if_prev_start_list = [[] for _ in range(100)] #if, 삼항 조건문 시작직전 함수 담는 스택
            if_ongoing_start_list = [[] for _ in range(100)] # else if문 함수 담는 스택
            if_function_in_list = [0] * 100 #if문 안에 함수가 있었는지 확인하는 리스트
            if_function_in_final_list = [0] * 100 #if문안에 함수가 하나라도 없었으면 1로 바꿈
            if_ongoing_start_level_list = []
            if_prev_start = [0] * 100
            else_if_ongoing_start = [0] * 100
            else_ongoing_start = [0] * 100
            if_ongoing_start = [0] * 100
            
            switch_prev_start_list = [[] for _ in range(100)] #switch case문 시작직전 함수 담는 스택
            switch_ongoing_start_list = [[] for _ in range(100)] #switch case문 중에 함수 담는 스택
            switch_function_in_list = [0] * 100
            switch_function_in_final_list = [0] * 100
            switch_ongoing_start_level_list = []
            switch_prev_start = [0] * 100
            case_ongoing_start = [0] * 100
            default_ongoing_start = [0] * 100
            switch_ongoing_start = [0] * 100

            while_prev_start_list = [[] for _ in range(100)] #if, 삼항 조건문 시작직전 함수 담는 스택
            while_ongoing_start_list = [[] for _ in range(100)] # else if문 함수 담는 스택
            while_function_in_list = [0] * 100 #if문 안에 함수가 있었는지 확인하는 리스트
            while_function_in_final_list = [0] * 100 #if문안에 함수가 하나라도 없었으면 1로 바꿈
            while_ongoing_start_level_list = []
            while_prev_start = [0] * 100
            #else_if_ongoing_start = [0] * 100
            #else_ongoing_start = [0] * 100
            while_ongoing_start = [0] * 100

            

            for i in range(0,len(callees)):
                callee = callees[i]
                control_flow_return_val = control_flow_check(callee)
                if control_flow_return_val == NORMAL_CONTROL:
                    if not prev_callee_list:
                        print_error_for_make_function('prev_callee_list error1')
                    if control_flow_list:
                        control_flow = control_flow_list[-1]
                        #control_flow_start_function_current_list = control_flow_start_function_list[-1]
                        #control_flow_end_function_current_list = control_flow_end_function_list[-1]
                        if control_flow == IF_CONTROL:
                            if if_ongoing_start[callee[2]] == 1:
                                if_function_in_list[callee[2]] = 1  #if안에 함수가 하나라도 있었다.
                                if control_flow == 1:
                                    control_flow = 0
                                if else_if_ongoing_start[callee[2]] == 1:                   #else if가 나오고 처음이면 ongoing에 넣음
                                    else_if_ongoing_start[callee[2]] = 2
                                    if_ongoing_start_list[callee[2]].append(callee)
                                    #control_flow_start_function_current_list.append(callee)
                                    #control_flow_end_function_current_list.append(callee)
                                    temp_if_ongoing_start_level = if_ongoing_start_level_list[-1]
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                elif else_if_ongoing_start[callee[2]] == 2:
                                    if_ongoing_start_list[callee[2]][-1] = callee #else if문의 마지막으로 호출된 함수를 최근으로 바꿔줌
                                    #control_flow_end_function_current_list[-1] = callee
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                        elif control_flow == SWITCH_CONTROL:
                            if switch_ongoing_start[callee[2]] == 1:
                                switch_function_in_list[callee[2]] = 1  #if안에 함수가 하나라도 있었다.
                                if control_flow == 1:
                                    control_flow = 0
                                if case_ongoing_start[callee[2]] == 1:                   #else if가 나오고 처음이면 ongoing에 넣음
                                    case_ongoing_start[callee[2]] = 2
                                    switch_ongoing_start_list[callee[2]].append(callee)
                                    #control_flow_start_function_current_list.append(callee)
                                    #control_flow_end_function_current_list.append(callee)
                                    temp_if_ongoing_start_level = if_ongoing_start_level_list[-1]
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                                elif case_ongoing_start[callee[2]] == 2:
                                    switch_ongoing_start_list[callee[2]][-1] = callee #else if문의 마지막으로 호출된 함수를 최근으로 바꿔줌
                                    #control_flow_end_function_current_list[-1] = callee
                                    make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                    else:   #진짜 아무것도 아닐때
                        make_connect(call_graph_matrix,call_graph_function_pos,caller,prev_callee_list,callee)
                    prev_callee_list.clear()
                    prev_callee_list.append(callee)
                #if 삼항연산자 체크
                else:
                    if control_flow_return_val == IF_CONTROL:
                        if callee[0] == 'start_info':
                            if callee[1] == 'if' or callee[1] == 'conditional':
                                #control_flow_start_function_list.append([])
                                #control_flow_end_function_list.append([])
                                control_flow_list.append(control_flow_return_val)
                                if_prev_start[callee[2]] = 1
                                else_if_ongoing_start[callee[2]] = 1
                                if_ongoing_start[callee[2]] = 1
                                for temp_prev_callee in prev_callee_list:
                                    if_prev_start_list[callee[2]].append(temp_prev_callee)
                                if_ongoing_start_level_list.append(callee[2])
                                prev_callee_list.clear()        #일관성을 위해서
                                for temp_if_prev_start in if_prev_start_list[callee[2]]:
                                    prev_callee_list.append(temp_if_prev_start)
                            elif callee[1] == 'else if':
                                if if_function_in_list[callee[2]] == 0:
                                    if_function_in_final_list[callee[2]] = 1
                                if_function_in_list[callee[2]] = 0
                                else_if_ongoing_start[callee[2]] = 1
                                if control_flow_end == 1:
                                    control_flow_end = 0
                                    for temp_prev_callee in prev_callee_list:
                                        if_ongoing_start_list[callee[2]].append(temp_prev_callee)
                                prev_callee_list.clear()
                                for temp_if_prev_start in if_prev_start_list[callee[2]]:
                                    prev_callee_list.append(temp_if_prev_start)
                            elif callee[1] == 'else':
                                if if_function_in_list[callee[2]] == 0:
                                    if_function_in_final_list[callee[2]] = 1
                                if_function_in_list[callee[2]] = 0
                                else_if_ongoing_start[callee[2]] = 1
                                else_ongoing_start[callee[2]] = 1
                                if control_flow_end == 1:
                                    control_flow_end = 0
                                    for temp_prev_callee in prev_callee_list:
                                        if_ongoing_start_list[callee[2]].append(temp_prev_callee)
                                prev_callee_list.clear()
                                for temp_if_prev_start in if_prev_start_list[callee[2]]:
                                    prev_callee_list.append(temp_if_prev_start)
                            else:
                                print_error_for_make_function('IF_CONTROL error1')
                        else:   #end_info
                            #print('before',prev_callee_list)
                            if control_flow_end == 1:
                                control_flow_end = 0
                                for temp_prev_callee in prev_callee_list:
                                    if_ongoing_start_list[callee[2]].append(temp_prev_callee)
                            prev_callee_list.clear()
                            if if_ongoing_start_list[callee[2]]: #if문 끝났는데 else if가 남아있다면
                                for temp_prev_callee in if_ongoing_start_list[callee[2]]:
                                    if function_not_in_list(temp_prev_callee,prev_callee_list) == 0:
                                        prev_callee_list.append(temp_prev_callee)
                                if_ongoing_start_list[callee[2]].clear()
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
                            #print('after',prev_callee_list)
                        
                            
                            if_prev_start[callee[2]] = 0
                            if_ongoing_start[callee[2]] = 0
                            else_if_ongoing_start[callee[2]] = 0
                            if_function_in_list[callee[2]] = 0
                            if_function_in_final_list[callee[2]] = 0
                            else_ongoing_start[callee[2]] = 0
                            if_prev_start_list[callee[2]].clear()
                            if_ongoing_start_list[callee[2]].clear()
                            if_ongoing_start_level_list.pop()
                            if control_flow_list:
                                control_flow_end = 1
                                if control_flow_list.pop() != IF_CONTROL:
                                    print('control flow is not if control!!')
                            else:
                                print_error_for_make_function('control flow empty!!')
                    
                    elif control_flow_return_val == SWITCH_CONTROL:
                        if callee[0] == 'start_info':
                            if callee[1] == 'switch':
                                control_flow_list.append(control_flow_return_val)
                                switch_prev_start[callee[2]] = 1
                                case_ongoing_start[callee[2]] = 1
                                switch_ongoing_start[callee[2]] = 1
                                for temp_prev_callee in prev_callee_list:
                                    switch_prev_start_list[callee[2]].append(temp_prev_callee)
                                switch_ongoing_start_level_list.append(callee[2])
                                prev_callee_list.clear()
                                for temp_switch_prev_start in switch_prev_start_list[callee[2]]:
                                    prev_callee_list.append(temp_switch_prev_start)
                            elif callee[1] == 'case':
                                if switch_function_in_list[callee[2]] == 0:
                                    switch_function_in_final_list[callee[2]] = 1
                                switch_function_in_list[callee[2]] = 0
                                case_ongoing_start[callee[2]] = 1
                                if control_flow_end == 1:
                                    control_flow_end = 0
                                    for temp_prev_callee in prev_callee_list:
                                        switch_ongoing_start_list[callee[2]].append(temp_prev_callee)
                                prev_callee_list.clear()
                                for temp_switch_prev_start in switch_prev_start_list[callee[2]]:
                                    prev_callee_list.append(temp_switch_prev_start)
                            elif callee[1] == 'default':
                                if switch_function_in_list[callee[2]] == 0:
                                    switch_function_in_final_list[callee[2]] = 1
                                switch_function_in_list[callee[2]] = 0
                                case_ongoing_start[callee[2]] = 1
                                default_ongoing_start[callee[2]] = 1
                                if control_flow_end == 1:
                                    control_flow_end = 0
                                    for temp_prev_callee in prev_callee_list:
                                        switch_ongoing_start_list[callee[2]].append(temp_prev_callee)
                                prev_callee_list.clear()
                                for temp_switch_prev_start in switch_prev_start_list[callee[2]]:
                                    prev_callee_list.append(temp_switch_prev_start)
                            else:
                                print_error_for_make_function('switch control error1')
                        else:
                            #print('before',prev_callee_list)
                            if control_flow_end == 1:
                                control_flow_end = 0
                                for temp_prev_callee in prev_callee_list:
                                    switch_ongoing_start_list[callee[2]].append(temp_prev_callee)
                            prev_callee_list.clear()
                            if switch_ongoing_start_list[callee[2]]: #if문 끝났는데 else if가 남아있다면
                                for temp_prev_callee in switch_ongoing_start_list[callee[2]]:
                                    if function_not_in_list(temp_prev_callee,prev_callee_list) == 0:
                                        prev_callee_list.append(temp_prev_callee)
                                switch_ongoing_start_list[callee[2]].clear()
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
                            #print('after',prev_callee_list)
                        
                            
                            switch_prev_start[callee[2]] = 0
                            switch_ongoing_start[callee[2]] = 0
                            case_ongoing_start[callee[2]] = 0
                            switch_function_in_list[callee[2]] = 0
                            switch_function_in_final_list[callee[2]] = 0
                            default_ongoing_start[callee[2]] = 0
                            switch_prev_start_list[callee[2]].clear()
                            switch_ongoing_start_list[callee[2]].clear()
                            switch_ongoing_start_level_list.pop()
                            if control_flow_list:
                                control_flow_end = 1
                                if control_flow_list.pop() != SWITCH_CONTROL:
                                    print('control flow is not switch control!!')
                            else:
                                print_error_for_make_function('control flow empty!!')
                    
                    elif control_flow_return_val == WHILE_CONTROL:
                        if callee[0] == 'start_info':
                            if callee[1] == 'for' or callee[1] == 'while':
                                #control_flow_start_function_list.append([])
                                #control_flow_end_function_list.append([])
                                control_flow_list.append(control_flow_return_val)
                                if_prev_start[callee[2]] = 1
                                else_if_ongoing_start[callee[2]] = 1
                                if_ongoing_start[callee[2]] = 1
                                for temp_prev_callee in prev_callee_list:
                                    if_prev_start_list[callee[2]].append(temp_prev_callee)
                                if_ongoing_start_level_list.append(callee[2])
                                prev_callee_list.clear()        #일관성을 위해서
                                for temp_if_prev_start in if_prev_start_list[callee[2]]:
                                    prev_callee_list.append(temp_if_prev_start)
                            else:
                                print_error_for_make_function('WHILE_CONTROL error1')
                        else:
                            print('hi')



                        

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
            for function_name, function_call_matrix in call_graph_matrix[caller].items():
                temp_connect_list = []
                for temp_index in range(0,len(function_call_matrix)):
                    temp_function_pos = {value: key for key, value in call_graph_function_pos[caller].items()}
                    if function_call_matrix[temp_index] == 1:
                        temp_connect_list.append(temp_function_pos[temp_index])
                print(function_name,temp_connect_list)
                

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
                    print(f"{caller} -> {callee[0],callee[1],callee[2]}")
        else:
            print(f"{caller} -> (끝)")
    print("Function Call Graph Lengh: "+str(call_length))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python3 '+ sys.argv[0] +' <C_FILE>')
        sys.exit(1)
    
    c_file = sys.argv[1]  # Get file from command-line argument
    
    if not os.path.isfile(c_file):
        print(f"Error: File '{c_file}' not found.")
        sys.exit(1)
    """
    function_workflow = extract_function_workflow(c_file)

    print("Function Workflow (Directed Edges):")
    for edge in function_workflow:
        print(f"{edge[0]} -> {edge[1]}")
    
    glibc_calls = extract_glibc_functions_from_c_code(c_file)
    print("Glibc functions used in the code:")
    for func in sorted(glibc_calls):
        print(func)
    """
    """
    function_graph = extract_function_call_graph_old(c_file)

    print("Function Call Graph Old:")
    call_length = 0
    for caller, callees in function_graph.items():
        if callees:
            for callee in callees:
                print(f"{caller} -> {callee[0],callee[1],callee[2]}")
                call_length += 1
        else:
            print(f"{caller} -> (끝)")
    print("Function Call Graph Old Lengh: "+str(call_length))

    #function_graph = extract_function_call_graph(c_file)
    """
    
    function_graph = extract_if_depth(c_file)
    print_call_graph(function_graph)
    make_matrix_from_function_graph(function_graph)


