"""Offline Windows minidump summary; raw stack candidates are NOT an unwound stack.

Does not execute the SDK or connect to the simulator. Uses only local symbols.
"""
import argparse
import ctypes as ct
from pathlib import Path
import struct


def read_dump(path):
    data = path.read_bytes()
    if data[:4] != b'MDMP':
        raise ValueError('Not a minidump')
    count, directory = struct.unpack_from('<II', data, 8)
    streams = {}
    for index in range(count):
        kind, size, rva = struct.unpack_from('<III', data, directory+12*index)
        streams[kind] = (size, rva)
    ex = streams[6][1]
    code = struct.unpack_from('<I', data, ex+8)[0]
    address = struct.unpack_from('<Q', data, ex+24)[0]
    context_size, context = struct.unpack_from('<II', data, ex+160)
    # This shipped SDK and its Python environment are AMD64.
    if context_size < 256:
        raise ValueError('Expected AMD64 CONTEXT')
    rsp = struct.unpack_from('<Q', data, context+152)[0]
    module_rva = streams[4][1]
    modules = []
    for index in range(struct.unpack_from('<I', data, module_rva)[0]):
        off = module_rva+4+108*index
        base, size = struct.unpack_from('<QI', data, off)
        name_rva = struct.unpack_from('<I', data, off+20)[0]
        length = struct.unpack_from('<I', data, name_rva)[0]
        name = data[name_rva+4:name_rva+4+length].decode('utf-16le')
        modules.append((base, size, name))
    memory = []
    if 5 in streams:
        rva = streams[5][1]
        for index in range(struct.unpack_from('<I', data, rva)[0]):
            base, size, start = struct.unpack_from('<QII', data, rva+4+16*index)
            memory.append((base, data[start:start+size]))
    if 9 in streams:
        rva = streams[9][1]
        count, start = struct.unpack_from('<QQ', data, rva)
        for index in range(count):
            base, size = struct.unpack_from('<QQ', data, rva+16+16*index)
            memory.append((base, data[start:start+size]))
            start += size
    candidates = []
    for base, block in memory:
        if base <= rsp < base+len(block):
            start = rsp-base
            for offset in range(start, min(len(block)-7, start+4096), 8):
                pointer = struct.unpack_from('<Q', block, offset)[0]
                if any(b <= pointer < b+s and Path(n).name.lower() in ('medusacore.dll', 'cpppackage.pyd')
                       for b, s, n in modules):
                    candidates.append((offset-start, pointer))
            break
    return code, address, modules, candidates


class SymbolInfo(ct.Structure):
    _fields_ = [('SizeOfStruct', ct.c_uint32), ('TypeIndex', ct.c_uint32),
                ('Reserved', ct.c_uint64*2), ('Index', ct.c_uint32), ('Size', ct.c_uint32),
                ('ModBase', ct.c_uint64), ('Flags', ct.c_uint32), ('Value', ct.c_uint64),
                ('Address', ct.c_uint64), ('Register', ct.c_uint32), ('Scope', ct.c_uint32),
                ('Tag', ct.c_uint32), ('NameLen', ct.c_uint32), ('MaxNameLen', ct.c_uint32),
                ('Name', ct.c_char*1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    args = parser.parse_args()
    code, address, modules, candidates = read_dump(args.dump)
    def describe(pointer):
        for base, size, name in modules:
            if base <= pointer < base+size:
                return f'{Path(name).name}+{pointer-base:#x}'
        return hex(pointer)
    print(f'{args.dump.name}: exception={code:#x} at {describe(address)}')
    root = Path(__file__).resolve().parents[3]
    symbol_dir = root/'ZBinV1'/'ZBin'
    dbg = ct.WinDLL('dbghelp', use_last_error=True)
    kernel = ct.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ct.c_void_p
    handle = kernel.GetCurrentProcess()
    dbg.SymInitializeW.argtypes = [ct.c_void_p, ct.c_wchar_p, ct.c_int]
    dbg.SymInitializeW.restype = ct.c_int
    dbg.SymLoadModuleExW.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_wchar_p, ct.c_wchar_p,
                                   ct.c_uint64, ct.c_uint32, ct.c_void_p, ct.c_uint32]
    dbg.SymLoadModuleExW.restype = ct.c_uint64
    dbg.SymFromAddr.argtypes = [ct.c_void_p, ct.c_uint64, ct.POINTER(ct.c_uint64), ct.c_void_p]
    dbg.SymFromAddr.restype = ct.c_int
    dbg.SymCleanup.argtypes = [ct.c_void_p]
    if not dbg.SymInitializeW(handle, str(symbol_dir), 0):
        raise ct.WinError(ct.get_last_error())
    try:
        for base, size, name in modules:
            if Path(name).name.lower() in ('medusacore.dll', 'cpppackage.pyd'):
                loaded = dbg.SymLoadModuleExW(handle, None, name, None, base, size, None, 0)
                print('Symbol module:', Path(name).name, 'loaded' if loaded else 'unavailable')
        print('Raw stack address candidates (may include non-call data):')
        for offset, pointer in candidates[:40]:
            buf = ct.create_string_buffer(ct.sizeof(SymbolInfo)+2048)
            info = SymbolInfo.from_buffer(buf)
            info.SizeOfStruct = ct.sizeof(SymbolInfo)
            info.MaxNameLen = 2048
            displacement = ct.c_uint64()
            name = ''
            if dbg.SymFromAddr(handle, pointer, ct.byref(displacement), buf):
                name = ct.string_at(ct.addressof(buf)+SymbolInfo.Name.offset, info.NameLen).decode(errors='replace')
                name += f'+{displacement.value:#x}'
            print(f'  rsp+{offset:#05x} {describe(pointer)} {name}')
    finally:
        dbg.SymCleanup(handle)


if __name__ == '__main__':
    main()
