import subprocess
import argparse
import json
import re

def get_glibc_function_list():
    result = subprocess.run(["nm", "-D", "/lib/x86_64-linux-gnu/libc.so.6"], capture_output=True, text=True)
    lines = result.stdout.splitlines()
    functions = []
    for line in lines:
        parts = line.split()
        if len(parts) == 3 and parts[1] in ['T', 'W']:
            functions.append(parts[2])
    return sorted(set(functions))

def build_call_graph(filename):
    call_graph = {}

    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or ':' not in line:
                continue  # 빈 줄이나 형식이 맞지 않는 줄은 건너뜀

            caller, callee = map(str.strip, line.split(':', 1))

            # 이미 있는 키면 리스트에 추가, 없으면 새 리스트 생성
            if callee == '':
                continue
            if caller == callee: #자기자신을 호출하는 경우와 아무것도 호출하지 않는 경우는 제외
                continue
            if caller in call_graph:
                #if callee not in call_graph[caller]:   //중복 없게 그냥 진행
                call_graph[caller].add(callee)
            else:
                call_graph[caller] = set([callee])
    return call_graph

def replace_callee_caller(call_graph):
    transform = 1
    visited_dict = {}
    sorted_call_graph = sorted(call_graph.items(), key=lambda item: len(item[1]))
    temp_call_graph = call_graph.copy()
    temp_temp_call_graph = {}
    callers =set([])
    count = 0
    different_count = 0
    temp_call_set = set([])
    different_set_list_for_debug = []
    for caller, callees in sorted_call_graph:
        if caller not in callers:
            callers.add(caller)
            visited_dict[caller] = set([])
        else:
            print('alreday!!'+str(caller))
    while transform == 1:
        transform = 0
        different_count = 0
        different_set_list_for_debug = []
        for caller, callees in sorted(temp_call_graph.items(), key=lambda item: len(item[1])):
            temp_temp_call_graph[caller] = callees.copy()
            while True:
                #if caller == 'fclose':
                #    print(temp_temp_call_graph[caller])
                temp_call_set = set([])
                for callee in temp_temp_call_graph[caller]:
                    if callee == caller:
                        continue
                    if callee in visited_dict[caller]:
                        if 'syscall' not in callee or '(' not in callee or ')' not in callee:
                            continue
                    if callee in callers:
                        temp_call_set = temp_call_set.union(temp_call_graph[callee])
                    elif 'syscall' in callee and '(' in callee and ')' in callee:
                        callee = callee.replace(" ","")
                        temp_call_set.add(callee)
                    visited_dict[caller].add(callee)
                if temp_temp_call_graph[caller] == temp_call_set:
                    break
                temp_temp_call_graph[caller] = temp_call_set.copy()
        temp_call_graph = temp_temp_call_graph.copy()
        temp_temp_call_graph = {}
     
    return temp_call_graph
                    
def get_syscall_map_from_file(filepath='syscall_list'):
    syscall_map = {}
    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or not line[0].isdigit():
                    continue
                parts = line.split(None, 1)  # 공백 또는 탭 기준으로 번호와 이름 분리
                if len(parts) == 2:
                    number, name = parts
                    syscall_map[int(number)] = name.strip()
    except Exception as e:
        print(f"Error reading syscall list from file '{filepath}': {e}")
        exit(1)
    return syscall_map

def extract_syscall_info(syscall_str, syscall_map):
    match = re.match(r'syscall\((\d+)\)', syscall_str)
    if match:
        num = int(match.group(1))
        name = syscall_map.get(num, 'unknown')
        return {"number": num, "name": name}
    return None
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyzing the glibc function call graph.")
    parser.add_argument("filename", help="Call graph filename (e.g., glibc.2.23.callgraph)")
    parser.add_argument("funcname", help="Name of the glibc function to analyze")
    parser.add_argument("-o", "--output", choices=["json", "plain"], default="plain",
                        help="Output format: 'json' or 'plain' (default: plain)")
    parser.add_argument("-l", "--list", action="store_true", help="List all functions in the call graph and glibc")

    args = parser.parse_args()

    filename = args.filename
    initial_func = args.funcname
    output_format = args.output

    graph = build_call_graph(filename)
    syscall_graph = replace_callee_caller(graph)
    syscall_map = get_syscall_map_from_file()

    if initial_func not in syscall_graph:
        print(f"{initial_func} does not exist")
        exit(1)

    # 시스템콜 정보 가공
    formatted_list = [
        extract_syscall_info(s, syscall_map)
        for s in syscall_graph[initial_func]
    ]
    formatted_list = [entry for entry in formatted_list if entry is not None]
    formatted_list.sort(key=lambda x: x['number'])

    if output_format == "json":
        syscall_numbers = [entry['number'] for entry in formatted_list]
        syscall_names = [entry['name'] for entry in formatted_list]

        output = {
            "function": initial_func,
            "count": len(formatted_list),
            "syscall_number": syscall_numbers,
            "syscall_name": syscall_names
        }

        json_str = '{\n'
        json_str += f'  "function": "{initial_func}",\n'
        json_str += f'  "count": {len(formatted_list)},\n'
        json_str += f'  "syscall_number": {syscall_numbers},\n'
        json_str += f'  "syscall_name": {json.dumps(syscall_names)}\n'
        json_str += '}'

        json_filename = f"{initial_func}.json"
        with open(json_filename, "w") as json_file:
            json_file.write(json_str)
        print(f"Saved syscall info to {json_filename}")

    else:  # plain 형식
        for entry in formatted_list:
            print(f"{initial_func}: {entry['number']} :{entry['name']}")

    if args.list:
        if not args.filename:
            print("Error: You must specify the call graph filename when using -l")
            exit(1)

        print("\nlist of all functions")
        glibc_funcs = get_glibc_function_list()
        for func in glibc_funcs:
            print(func)
        exit(0)