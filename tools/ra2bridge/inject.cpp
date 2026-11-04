// inject.exe <pid> <dll path> - load a DLL into a 32-bit process (CreateRemoteThread + LoadLibraryA).
// Built as 32-bit so the LoadLibraryA address matches the target's kernel32.
#include <windows.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>

int main(int argc, char** argv) {
	if (argc != 3) {
		fprintf(stderr, "usage: inject <pid> <dll>\n");
		return 2;
	}
	DWORD pid = static_cast<DWORD>(strtoul(argv[1], nullptr, 10));
	char path[MAX_PATH];
	if (!GetFullPathNameA(argv[2], MAX_PATH, path, nullptr)) {
		fprintf(stderr, "bad dll path\n");
		return 2;
	}
	HANDLE proc = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION
		| PROCESS_VM_WRITE | PROCESS_VM_READ, FALSE, pid);
	if (!proc) {
		fprintf(stderr, "OpenProcess failed (%lu)\n", GetLastError());
		return 1;
	}
	SIZE_T size = strlen(path) + 1;
	void* remote = VirtualAllocEx(proc, nullptr, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
	if (!remote || !WriteProcessMemory(proc, remote, path, size, nullptr)) {
		fprintf(stderr, "writing the path failed (%lu)\n", GetLastError());
		return 1;
	}
	auto loadLibrary = reinterpret_cast<LPTHREAD_START_ROUTINE>(
		GetProcAddress(GetModuleHandleA("kernel32.dll"), "LoadLibraryA"));
	HANDLE thread = CreateRemoteThread(proc, nullptr, 0, loadLibrary, remote, 0, nullptr);
	if (!thread) {
		fprintf(stderr, "CreateRemoteThread failed (%lu)\n", GetLastError());
		return 1;
	}
	WaitForSingleObject(thread, 10000);
	DWORD module = 0;
	GetExitCodeThread(thread, &module);
	VirtualFreeEx(proc, remote, 0, MEM_RELEASE);
	CloseHandle(thread);
	CloseHandle(proc);
	if (!module) {
		fprintf(stderr, "LoadLibrary failed inside the game (wrong exe version, or already injected?)\n");
		return 1;
	}
	printf("injected %s into %lu\n", path, pid);
	return 0;
}
