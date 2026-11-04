// ra2bridge.dll - injected into gamemd.exe (Yuri's Revenge 1.001).
//
// A server thread accepts one TCP client at a time on 127.0.0.1:47800 and reads line commands.
// Commands are executed on the game's own thread: a 5-byte JMP at the start of MainLoop
// (0x55D360) calls OnMainLoop() once per frame, which drains the queue. Player actions are issued
// as EventClass events into OutList, exactly like clicks on the sidebar, so the game validates
// and schedules them the normal way.
//
// Protocol: one command per line, one reply line per command ("OK ..." or "ERR ...").
//   PING                 -> OK frame=<n> mode=<game mode>
//   PRODUCE <TypeID>     -> start building a unit/building (like clicking its cameo)
//   PLACE <TypeID> [x y] -> place a finished building; without x y, the nearest valid cell
//   ABANDON <TypeID>     -> cancel production
//   DEPLOY <unit>        -> deploy (MCV -> construction yard, GI sandbags, ...)
//   MOVE <unit> <x> <y>  -> move order to a cell
//   ATTACK <unit> <target> -> attack an enemy object (<target> = memory address, any owner but ours)
//   CAMERA <x> <y>       -> centre our own view on a cell (display only, sends no event)
//   <unit> is a memory address ("0x1234abcd", as monitor.py reports) or a type ID (first owned one)
//
// Safety: commands are refused in Internet games; the socket only listens on localhost.

#include <winsock2.h>  // before YRpp (SessionClass.h pulls winsock.h)
#include <ws2tcpip.h>

#include <BuildingClass.h>
#include <BuildingTypeClass.h>
#include <DisplayClass.h>
#include <EventClass.h>
#include <FactoryClass.h>
#include <HouseClass.h>
#include <MapClass.h>
#include <TacticalClass.h>
#include <SessionClass.h>
#include <TechnoClass.h>
#include <TechnoTypeClass.h>

#include <algorithm>
#include <condition_variable>
#include <cstdio>
#include <deque>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>

#pragma comment(lib, "ws2_32.lib")

namespace {

constexpr unsigned short kPort = 47800;
constexpr DWORD kMainLoop = 0x55D360;  // starts with: mov al, [0xA8E9A0] (5 bytes, no relocation)

struct Request {
	std::string line;
	std::string reply;
	bool done = false;
};

std::mutex g_mutex;
std::condition_variable g_cv;
std::deque<std::shared_ptr<Request>> g_queue;

// ------------------------------------------------------------------ helpers

std::string Fmt(const char* fmt, ...) {
	char buf[512];
	va_list ap;
	va_start(ap, fmt);
	vsnprintf(buf, sizeof(buf), fmt, ap);
	va_end(ap);
	return buf;
}

const char* ModeName(GameMode m) {
	switch (m) {
	case GameMode::Campaign: return "campaign";
	case GameMode::Skirmish: return "skirmish";
	case GameMode::LAN: return "lan";
	case GameMode::Internet: return "internet";
	}
	return "unknown";
}

// Where to start searching for a building spot: our construction yard, else the base centre.
CellStruct BaseCell(HouseClass* house) {
	for (auto pBld : BuildingClass::Array) {
		if (pBld->Owner == house && pBld->Type->ConstructionYard && pBld->IsOnMap) {
			CellStruct cell;
			pBld->GetMapCoords(&cell);
			return cell;
		}
	}
	return house->GetBaseCenter();
}

bool CanPlaceAt(BuildingTypeClass* type, HouseClass* house, CellStruct cell) {
	return type->CanPlaceHere(&cell, house)
		&& DisplayClass::Instance.PassesProximityCheck(type, house->ArrayIndex, type->FoundationData, &cell);
}

// Nearest valid cell on growing square rings around the base.
bool FindPlacement(BuildingTypeClass* type, HouseClass* house, CellStruct& out) {
	const CellStruct base = BaseCell(house);
	for (short r = 0; r <= 24; ++r) {
		for (short dy = -r; dy <= r; ++dy) {
			for (short dx = -r; dx <= r; ++dx) {
				if (std::abs(dx) != r && std::abs(dy) != r) {
					continue;  // ring only
				}
				CellStruct cell { static_cast<short>(base.X + dx), static_cast<short>(base.Y + dy) };
				if (CanPlaceAt(type, house, cell)) {
					out = cell;
					return true;
				}
			}
		}
	}
	return false;
}

// ------------------------------------------------------------------ commands

std::string CmdProduce(HouseClass* house, TechnoTypeClass* type) {
	auto result = house->CanBuild(type, false, false);
	if (result != CanBuildResult::Buildable) {
		return Fmt("ERR cannot build %s now (CanBuild=%d)", type->ID, static_cast<int>(result));
	}
	const bool naval = type->Naval;
	EventClass ev(house->ArrayIndex, EventType::Produce, static_cast<int>(type->WhatAmI()),
		type->GetArrayIndex(), naval);
	if (!EventClass::OutList.Add(ev)) {
		return "ERR event queue full";
	}
	return Fmt("OK produce %s", type->ID);
}

std::string CmdAbandon(HouseClass* house, TechnoTypeClass* type) {
	EventClass ev(house->ArrayIndex, EventType::Abandon, static_cast<int>(type->WhatAmI()),
		type->GetArrayIndex(), type->Naval);
	if (!EventClass::OutList.Add(ev)) {
		return "ERR event queue full";
	}
	return Fmt("OK abandon %s", type->ID);
}

std::string CmdPlace(HouseClass* house, TechnoTypeClass* techno, std::istringstream& args) {
	if (techno->WhatAmI() != AbstractType::BuildingType) {
		return Fmt("ERR %s is not a building", techno->ID);
	}
	auto type = static_cast<BuildingTypeClass*>(techno);
	FactoryClass* factory = house->GetPrimaryFactory(AbstractType::BuildingType, type->Naval, type->BuildCat);
	if (!factory || !factory->Object || factory->Object->GetTechnoType() != type) {
		return Fmt("ERR %s is not in production", type->ID);
	}
	if (!factory->IsDone()) {
		return Fmt("ERR %s is not finished yet", type->ID);
	}
	CellStruct cell;
	int x, y;
	if (args >> x >> y) {
		cell = { static_cast<short>(x), static_cast<short>(y) };
		if (!CanPlaceAt(type, house, cell)) {
			return Fmt("ERR cannot place %s at %d %d", type->ID, x, y);
		}
	} else if (!FindPlacement(type, house, cell)) {
		return Fmt("ERR no valid spot for %s near the base", type->ID);
	}
	EventClass ev(house->ArrayIndex, EventType::Place, AbstractType::Building, type->GetArrayIndex(),
		type->Naval, cell);
	if (!EventClass::OutList.Add(ev)) {
		return "ERR event queue full";
	}
	return Fmt("OK place %s %d %d", type->ID, cell.X, cell.Y);
}

// A unit of ours, by memory address ("0x1234abcd", as monitor.py reports) or by type ID (first one).
TechnoClass* FindOwnUnit(HouseClass* house, const std::string& ref) {
	DWORD addr = 0;
	if (ref.rfind("0x", 0) == 0 || ref.rfind("0X", 0) == 0) {
		addr = strtoul(ref.c_str(), nullptr, 16);
	}
	for (auto pTechno : TechnoClass::Array) {
		if (pTechno->Owner != house || !pTechno->IsAlive || !pTechno->IsOnMap) {
			continue;
		}
		if (addr ? reinterpret_cast<DWORD>(pTechno) == addr : _stricmp(pTechno->GetTechnoType()->ID, ref.c_str()) == 0) {
			return pTechno;
		}
	}
	return nullptr;
}

std::string CmdDeploy(HouseClass* house, TechnoClass* unit) {
	EventClass ev(house->ArrayIndex, EventType::Deploy);
	ev.Deploy.Whom = TargetClass(unit);
	if (!EventClass::OutList.Add(ev)) {
		return "ERR event queue full";
	}
	return Fmt("OK deploy 0x%08x", reinterpret_cast<DWORD>(unit));
}

std::string CmdMove(HouseClass* house, TechnoClass* unit, std::istringstream& args) {
	int x, y;
	if (!(args >> x >> y)) {
		return "ERR usage: MOVE <unit> <x> <y>";
	}
	CellStruct cell { static_cast<short>(x), static_cast<short>(y) };
	EventClass ev(house->ArrayIndex, TargetClass(unit), Mission::Move, TargetClass(), TargetClass(cell), TargetClass());
	if (!EventClass::OutList.Add(ev)) {
		return "ERR event queue full";
	}
	return Fmt("OK move 0x%08x %d %d", reinterpret_cast<DWORD>(unit), x, y);
}

// Any live object on the map that is not ours, by memory address.
TechnoClass* FindEnemy(HouseClass* house, const std::string& ref) {
	if (ref.rfind("0x", 0) != 0 && ref.rfind("0X", 0) != 0) {
		return nullptr;
	}
	const DWORD addr = strtoul(ref.c_str(), nullptr, 16);
	for (auto pTechno : TechnoClass::Array) {
		if (reinterpret_cast<DWORD>(pTechno) == addr) {
			return pTechno->IsAlive && pTechno->IsOnMap && pTechno->Owner != house ? pTechno : nullptr;
		}
	}
	return nullptr;
}

std::string CmdAttack(HouseClass* house, TechnoClass* unit, std::istringstream& args) {
	std::string ref;
	if (!(args >> ref)) {
		return "ERR usage: ATTACK <unit> <target address>";
	}
	TechnoClass* target = FindEnemy(house, ref);
	if (!target) {
		return "ERR no live enemy object at " + ref;
	}
	EventClass ev(house->ArrayIndex, TargetClass(unit), Mission::Attack, TargetClass(target), TargetClass(), TargetClass());
	if (!EventClass::OutList.Add(ev)) {
		return "ERR event queue full";
	}
	return Fmt("OK attack 0x%08x 0x%08x", reinterpret_cast<DWORD>(unit), reinterpret_cast<DWORD>(target));
}

// Our own view only: no event is sent, so nothing about the match changes.
std::string CmdCamera(std::istringstream& args) {
	int x, y;
	if (!(args >> x >> y)) {
		return "ERR usage: CAMERA <x> <y>";
	}
	CellStruct cell { static_cast<short>(x), static_cast<short>(y) };
	CellClass* pCell = MapClass::Instance.TryGetCellAt(cell);
	if (!pCell) {
		return Fmt("ERR no cell %d %d", x, y);
	}
	CoordStruct coords = pCell->GetCoords();
	TacticalClass::Instance->SetTacticalPosition(&coords);
	return Fmt("OK camera %d %d", x, y);
}

std::string Execute(const std::string& line) {
	std::istringstream in(line);
	std::string cmd, id;
	in >> cmd;
	for (auto& c : cmd) c = static_cast<char>(toupper(static_cast<unsigned char>(c)));

	if (cmd == "PING") {
		return Fmt("OK frame=%d mode=%s", static_cast<int>(Unsorted::CurrentFrame), ModeName(SessionClass::Instance.GameMode));
	}
	if (cmd == "CAMERA") {
		if (!HouseClass::CurrentPlayer) {
			return "ERR not in a game";
		}
		return CmdCamera(in);
	}
	static const char* const known[] = { "PRODUCE", "ABANDON", "PLACE", "DEPLOY", "MOVE", "ATTACK" };
	if (std::none_of(std::begin(known), std::end(known), [&](const char* k) { return cmd == k; })) {
		return "ERR unknown command " + cmd;
	}
	if (SessionClass::Instance.GameMode == GameMode::Internet) {
		return "ERR refused: internet game";
	}
	HouseClass* house = HouseClass::CurrentPlayer;
	if (!house) {
		return "ERR not in a game";
	}
	if (!(in >> id)) {
		return "ERR usage: " + cmd + " <TypeID|unit>";
	}
	if (cmd == "DEPLOY" || cmd == "MOVE" || cmd == "ATTACK") {
		TechnoClass* unit = FindOwnUnit(house, id);
		if (!unit) {
			return "ERR no unit of ours matches " + id;
		}
		if (cmd == "ATTACK") return CmdAttack(house, unit, in);
		return cmd == "DEPLOY" ? CmdDeploy(house, unit) : CmdMove(house, unit, in);
	}
	TechnoTypeClass* type = TechnoTypeClass::Find(id.c_str());
	if (!type) {
		return "ERR unknown type " + id;
	}
	if (cmd == "PRODUCE") return CmdProduce(house, type);
	if (cmd == "ABANDON") return CmdAbandon(house, type);
	if (cmd == "PLACE") return CmdPlace(house, type, in);
	return "ERR unknown command " + cmd;
}

void ExecuteInto(Request* req) {
	req->reply = Execute(req->line);
}

// SEH guard: a bad read inside the game must not take the game down. (No C++ objects allowed here.)
bool ExecuteGuarded(Request* req) {
	__try {
		ExecuteInto(req);
		return true;
	} __except (EXCEPTION_EXECUTE_HANDLER) {
		return false;
	}
}

// ------------------------------------------------------------------ game thread

void __stdcall OnMainLoop() {
	std::deque<std::shared_ptr<Request>> work;
	{
		std::lock_guard<std::mutex> lock(g_mutex);
		work.swap(g_queue);
	}
	if (work.empty()) {
		return;
	}
	for (auto& req : work) {
		if (!ExecuteGuarded(req.get())) {
			req->reply = "ERR exception while executing";
		}
	}
	{
		std::lock_guard<std::mutex> lock(g_mutex);
		for (auto& req : work) req->done = true;
	}
	g_cv.notify_all();
}

__declspec(naked) void HookMainLoop() {
	__asm {
		pushad
		pushfd
		call OnMainLoop
		popfd
		popad
		mov al, byte ptr ds:[0xA8E9A0]  // the instruction we overwrote
		push 0x55D365                    // back into MainLoop after the 5 overwritten bytes
		ret
	}
}

bool InstallHook() {
	auto target = reinterpret_cast<BYTE*>(kMainLoop);
	const BYTE expected[5] = { 0xA0, 0xA0, 0xE9, 0xA8, 0x00 };
	if (memcmp(target, expected, sizeof(expected)) != 0) {
		return false;  // different exe version, or already hooked
	}
	DWORD old;
	if (!VirtualProtect(target, 5, PAGE_EXECUTE_READWRITE, &old)) {
		return false;
	}
	target[0] = 0xE9;
	*reinterpret_cast<int32_t*>(target + 1) =
		static_cast<int32_t>(reinterpret_cast<DWORD>(&HookMainLoop) - (kMainLoop + 5));
	VirtualProtect(target, 5, old, &old);
	FlushInstructionCache(GetCurrentProcess(), target, 5);
	return true;
}

// ------------------------------------------------------------------ server thread

std::string Submit(const std::string& line) {
	auto req = std::make_shared<Request>();
	req->line = line;
	std::unique_lock<std::mutex> lock(g_mutex);
	g_queue.push_back(req);
	if (!g_cv.wait_for(lock, std::chrono::seconds(5), [&] { return req->done; })) {
		return "ERR timeout: the game loop is not running (menu or loading?)";
	}
	return req->reply;
}

void Serve(SOCKET client) {
	std::string buffer;
	char chunk[1024];
	for (;;) {
		int n = recv(client, chunk, sizeof(chunk), 0);
		if (n <= 0) {
			return;
		}
		buffer.append(chunk, n);
		size_t pos;
		while ((pos = buffer.find('\n')) != std::string::npos) {
			std::string line = buffer.substr(0, pos);
			buffer.erase(0, pos + 1);
			if (!line.empty() && line.back() == '\r') line.pop_back();
			if (line.empty()) continue;
			std::string reply = Submit(line) + "\n";
			send(client, reply.data(), static_cast<int>(reply.size()), 0);
		}
	}
}

void ServerThread() {
	WSADATA wsa;
	if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
		return;
	}
	SOCKET listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
	sockaddr_in addr {};
	addr.sin_family = AF_INET;
	addr.sin_port = htons(kPort);
	addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	if (bind(listener, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0 || listen(listener, 1) != 0) {
		closesocket(listener);
		return;
	}
	for (;;) {
		SOCKET client = accept(listener, nullptr, nullptr);
		if (client == INVALID_SOCKET) {
			continue;
		}
		Serve(client);
		closesocket(client);
	}
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
	if (reason == DLL_PROCESS_ATTACH) {
		DisableThreadLibraryCalls(module);
		if (!InstallHook()) {
			return FALSE;  // LoadLibrary fails -> the injector reports it
		}
		std::thread(ServerThread).detach();
	}
	return TRUE;
}
