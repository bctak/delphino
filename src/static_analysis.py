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

        has_else_list = []
        has_else = 0
        if_first_ongoing = 0     #IfStmt를 처음으로 감지하는 부분, if의 조건문부분을 탐지하기 위해 사용
        if_conditional_ongoing = 0
        if_conditional_depth = 0 #IfStmt조건문 부분을 탐지하기 위해 사용, if 조건문 부분이 depth가 다시 처음으로 동일해지는 부분을 캐치
        if_conditional_depth_list = []


        if_ongoing_depth = []
        if_level = 0
        if_level_else_if_list = [] #else if문들을 wide하게 표현하기 위해 사용되는 리스트
        if_level_else_if_list.append(0)
        if_level_else_if_list_not_append = 0

        conditional_operator_first_ongoing = 0
        conditional_operator_ongoing = 0
        conditional_operator_ongoing_list = []
        first_conditional_operator_depth = 0
        first_conditional_operator_depth_list =[]
        conditional_ongoing_depth = []
        conditional_if_level_plus = 0

        if_level_else_if_list_not_append_conditional = 0

        

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
                            if function_stack[-1][4] == 'clone' or function_stack[-1][4] == 'pthread_create':
                                function_calls[current_function].append(function_stack.pop())
                    if not function_call_depth:
                        break    

            if if_ongoing_depth:
                while current_depth <= if_ongoing_depth[len(if_ongoing_depth)-1]:
                    if if_level <= 0:
                        print_error('if_level error1',lines,i)
                    if_level -= 1
                    if_ongoing_depth.pop()
                    if_level_else_if_list.pop()
                    if_level_else_if_list_not_append = 0
                    if not if_level_else_if_list:
                        print_error('if_level_else_if_list error1 !!!!',lines,i)
                    if not if_ongoing_depth:
                        break

            #삼항 연산 조건문

            if conditional_ongoing_depth:
                while current_depth <= conditional_ongoing_depth[-1]:
                    if if_level <= 0:
                        print_error('if_level error3',lines,i)
                    if_level -= 1
                    conditional_ongoing_depth.pop()
                    if_level_else_if_list.pop()
                    first_conditional_operator_depth_list.pop()
                    conditional_operator_ongoing_list.pop()
                    if_level_else_if_list_not_append_conditional = 0
                    conditional_operator_ongoing = 0
                    if not conditional_ongoing_depth:
                        break                

            if "ConditionalOperator" in line:
                conditional_operator_first_ongoing = 1
                temp_conditional_current_depth = get_first_alpha_index(line)
                conditional_ongoing_depth.append(temp_conditional_current_depth)
                conditional_if_level_plus = 0
                if if_level_else_if_list_not_append_conditional == 0:
                    if_level_else_if_list.append(0)
                else:
                    if_level_else_if_list_not_append_conditional = 0
            
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
                            conditional_if_level_plus = 1
                        conditional_operator_ongoing = 3
                        conditional_operator_ongoing_list[-1] = 3
                elif conditional_operator_ongoing == 3:
                    if current_depth == first_conditional_operator_depth:
                        if if_level_else_if_list and len(if_level_else_if_list) > if_level: #1번째가 실제로 if_level 1단계이다.
                            print('hello1')
                            if_level_else_if_list[if_level] += 1
                        conditional_operator_ongoing = 0
                else:
                    conditional_operator_ongoing = 2
                    conditional_operator_ongoing_list.append(conditional_operator_ongoing)


            #일반 조건문
            if if_conditional_depth_list:
                if_conditional_depth = if_conditional_depth_list[-1]    #마지막 가져오기

            if has_else_list:
                #print(has_else_list)
                has_else = has_else_list[-1]
            if has_else == 1:
                temp_if_end_depth = get_first_backtick_index(line)
                if temp_if_end_depth + 2 == if_conditional_depth:
                    if if_level <= 0:
                        print_error('if_level error2',lines,i)
                    if if_level_else_if_list and len(if_level_else_if_list) > if_level: #1번째가 실제로 if_level 1단계이다.
                        print('hello2')
                        if_level_else_if_list[if_level] += 1
                    else:
                        print_error('if_level_else_if_list error2 !!!!',lines,i)
                    #print(if_level_else_if_list,if_level)
                    if "IfStmt" in line:
                        if_ongoing_depth.pop()
                        if_level -= 1
                    has_else = 0
                    has_else_list.pop()
                    if_conditional_depth_list.pop()
                    if_level_else_if_list_not_append = 1

            if "IfStmt" in line:
                if if_level_else_if_list_not_append == 0:
                    if_level_else_if_list.append(0)
                else:
                    if_level_else_if_list_not_append = 0
                if "has_else" in line:
                    #has_else = 1
                    has_else_list.append(1)
                if_first_ongoing = 1
                temp_if_current_depth = get_first_alpha_index(line)
                if_ongoing_depth.append(temp_if_current_depth)
            
            if if_first_ongoing > 0:
                if if_first_ongoing == 2:
                    if_first_ongoing = 0
                    if_conditional_depth = get_first_alpha_index(line)
                    if_conditional_depth_list.append(if_conditional_depth)
                    if_conditional_ongoing = 1
                else:
                    if_first_ongoing = 2


            if if_conditional_ongoing > 0:
                if if_conditional_ongoing == 2:
                    if current_depth == if_conditional_depth:
                        if_level += 1
                        if_conditional_ongoing = 0
                else:
                    if_conditional_ongoing = 2   


            #조건문 관련 끝 if_level을 통해서만 조절        

            # 현재 어떤 함수 내부인지 찾기 (FunctionDecl 사용)
            if "FunctionDecl" in line:
                match = re.search(r"FunctionDecl\s+[^\']+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*'", line)
                if match:
                    current_function = match.group(1)
                    if current_function in user_functions:
                        function_calls[current_function] = []  # 함수 호출 그래프 초기화

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
                                function_stack.append([current_call_depth,i,if_level,if_level_else_if_list[if_level],called_function])
                                #print(called_function,function_call_depth,function_stack)
                                #function_calls[current_function].append([call_depth,i,called_function])
                            if "clone" == called_function:
                                for k in range(j + 1, min(j + 20, len(lines))):  # CallExpr 이후 몇 줄 체크
                                    if "DeclRefExpr" in lines[k]:
                                        match2 = re.search(r"DeclRefExpr.*Function\s+0x[0-9a-f]+\s+'([a-zA-Z_][a-zA-Z0-9_]*)'", lines[k])
                                        if match2:
                                            cloned_function = match2.group(1)
                                            if cloned_function in all_functions or cloned_function in user_functions:
                                                function_stack.append([current_call_depth,i,if_level,if_level_else_if_list[if_level],cloned_function])
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
                                                function_stack.append([current_call_depth,i,if_level,if_level_else_if_list[if_level],pthread_create_function])
                                            break
                        break

        if function_stack and current_function:
            for i in reversed(range(0, len(function_stack))):
                function_calls[current_function].append(function_stack[i])            
               
        print(len(if_level_else_if_list),if_level_else_if_list,'!!!!!!!!!!!!!!!!!!!!!!')


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

    print("Function Call Graph:")
    call_length = 0
    for caller, callees in function_graph.items():
        if callees:
            for callee in callees:
                print(f"{caller} -> {callee[0],callee[1],callee[2],callee[3],callee[4]}")
                call_length += 1
        else:
            print(f"{caller} -> (끝)")
    print("Function Call Graph Lengh: "+str(call_length))