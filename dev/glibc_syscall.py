import subprocess
import argparse
import json
import re

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
                    
def get_syscall_map():
    syscall_map = {}
    try:
        output = subprocess.check_output(['ausyscall', '--dump'], text=True)
        for line in output.strip().split('\n'):
            if line.strip() and line[0].isdigit():
                number, name = line.strip().split(None, 1)
                syscall_map[int(number)] = name
    except Exception as e:
        print(f"Error reading syscall map: {e}")
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

    args = parser.parse_args()
    filename = args.filename
    initial_func = args.funcname

    graph = build_call_graph(filename)
    syscall_graph = replace_callee_caller(graph)
    syscall_map = get_syscall_map()

    output = {}

    if initial_func in syscall_graph:
        formatted_list = [
            extract_syscall_info(s, syscall_map)
            for s in syscall_graph[initial_func]
        ]
        formatted_list = [entry for entry in formatted_list if entry is not None]
        formatted_list.sort(key=lambda x: x['number'])
        formatted_list = [entry for entry in formatted_list if entry is not None]
        output[initial_func] = formatted_list
    else:
        print(f"{initial_func} does not exist")
        exit(1)
        output[initial_func] = []

    # JSON 파일로 저장
    json_filename = f"{initial_func}.json"
    with open(json_filename, "w") as json_file:
        json.dump(output, json_file, indent=2)

    print(f"Saved syscall info to {json_filename}")
