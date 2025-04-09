# C_static_analysis

# The project to analyze C code and extract system calls used by the program
## Overview
This project extracts libc functions using Clang and libc symbols, identifies the relationship between libc functions and system calls, and ultimately extracts system calls from C code. In the future, system call information can be used for security, program structure analysis, and other purposes.

## Getting Started

### Prerequisites

- **Clang:** Clang must be installed on the PC to analyze C code.
```
sudo apt update
sudo apt install clang
```

### Usage

#### Run static analysis program

```
cd C_static_analysis/src/
python3 static_analysis.py [c source code name]
```

Pass the filename of the C code to be analyzed as the first argument. As a result, the glibc functions used in the C code will be displayed.


## Project Structure
```
Syscall_sequence_defense/
│
├── src/
│   ├── static_analysis.py                  # C language analysis program
│   ├── glibc_syscall.py                    # A program converting glibc functions to system calls.
│
├── example/
│   ├── static_v1.py                        # C language analysis program
│   ├── dirtycred.c                         # Example C code (Dirty Cred attack code)
│
├── callgraph/
│   ├── glibc.2.23.callgraph                # glibc call graph
│   ├── glibc.2.31.callgraph                # glibc call graph
│   ├── glibc.callgraph                     # glibc call graph
│
└── README.md                               # Project README file
```
