import time

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

def print_sorted_call_graph_by_callee_count(call_graph):
    print("\n[리스트 길이 기준 오름차순 출력]")
    for caller, callees in sorted(call_graph.items(), key=lambda item: len(item[1])):
        print(f"{caller} -> {callees} (총 {len(callees)}개)")

def replace_single_callee_callers_recursive(call_graph):
    # 1. callee가 1개뿐인 caller들을 찾기
    single_callee_map = {k: v[0] for k, v in call_graph.items() if len(v) == 1}

    # 2. 다단계 치환을 위한 재귀 함수
    def resolve_final_callee(func):
        seen = set()
        while func in single_callee_map and func not in seen:
            seen.add(func)
            func = single_callee_map[func]
        return func

    # 3. 전체 그래프 순회하며 치환
    updated_graph = {}
    for caller, callees in call_graph.items():
        new_callees = []
        for callee in callees:
            new_callees.append(resolve_final_callee(callee))
        updated_graph[caller] = new_callees

    return updated_graph

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
                    

# 사용 예시
if __name__ == "__main__":
    filename = "glibc.2.23.callgraph"
    graph = build_call_graph(filename)

    syscall_graph = replace_callee_caller(graph)

    while True:
        key = input("함수 이름을 입력하세요 (종료하려면 'exit'): ").strip()
        if key == 'exit':
            break
        if key in syscall_graph:
            syscall_list = list(syscall_graph[key])
            syscall_list.sort()
            print(f"{key}가 호출하는 함수들: {syscall_list}","길이 "+str(len(syscall_graph[key])))
        else:
            print(f"{key}는 호출 정보를 가지고 있지 않습니다.")
