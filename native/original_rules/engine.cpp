#include "engine.h"
#include <Windows.h>
#include <algorithm>
#include <cstring>
#include <stdexcept>

namespace tmserver::original
{
namespace
{

constexpr auto imageBase  = std::uintptr_t{ 0x10000000 };
constexpr auto imageSize  = std::size_t{ 0x35B000 };
constexpr auto dataOffset = std::size_t{ 0x204000 };
constexpr auto playerRva  = std::size_t{ 0x2B63E0 };
constexpr auto randomRva  = std::size_t{ 0x1E2E44 };

template <typename T>
void write(std::uint8_t* base, std::size_t offset, T value) noexcept
{
    std::memcpy(base + offset, &value, sizeof(value));
}

template <typename T>
auto read(const std::uint8_t* base, std::size_t offset) noexcept -> T
{
    auto value = T{};
    std::memcpy(&value, base + offset, sizeof(value));
    return value;
}

void protect(void* address, std::size_t size, DWORD protection)
{
    auto previous = DWORD{};
    if (!VirtualProtect(address, size, protection, &previous))
    {
        throw std::runtime_error("Cannot protect original code image");
    }
}

__declspec(naked) void selectHand(std::uint32_t, void*)
{
    __asm
    {
        push ebx
        push esi
        mov eax, [esp + 12]
        mov esi, [esp + 16]
        mov edx, 100C42DEh
        call edx
        pop esi
        pop ebx
        ret
    }
}

__declspec(naked) void selectTarget(void*)
{
    __asm
    {
        push ebp
        mov ebp, [esp + 8]
        sub esp, 50h // The original fragment writes [esp + 48h].
        xor eax, eax // The isolated operation uses simulated seat zero.
        mov edx, 100CEB2Bh
        call edx
        add esp, 50h
        pop ebp
        ret
    }
}

void seedBattle(std::uint8_t* card, const std::uint32_t (&stats)[4], std::int32_t modifier, std::uint32_t property) noexcept
{
    for (auto index = std::size_t{}; index < 4; ++index)
    {
        write<std::uint8_t>(card, 8 + index, static_cast<std::uint8_t>(stats[index]));
    }

    write<std::uint8_t>(card, 0x24, static_cast<std::uint8_t>(property));
    write<std::uint8_t>(card, 0x26, static_cast<std::uint8_t>(modifier));
}

void battleResult(const std::uint8_t* card, std::uint32_t (&result)[4]) noexcept
{
    result[0] = read<std::uint16_t>(card, 0x8E);
    result[1] = read<std::uint16_t>(card, 0x8C);
    result[2] = read<std::uint8_t>(card, 0x29);
    result[3] = read<std::uint8_t>(card, 0x2A);
}

} // namespace

void ReleaseMapping::operator()(std::uint8_t* mapping) const noexcept
{
    VirtualFree(mapping, 0, MEM_RELEASE);
}

Engine::Engine(std::span<const std::uint8_t> image, std::span<const std::uint8_t> catalog, RandomThunk random)
{
    if (image.size() != imageSize || catalog.size() != catalog_.size())
    {
        throw std::invalid_argument("Unexpected original image or catalogue size");
    }

    image_.reset(static_cast<std::uint8_t*>(VirtualAlloc(reinterpret_cast<void*>(imageBase), imageSize, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)));
    if (!image_)
    {
        throw std::runtime_error("Original image base is unavailable");
    }

    std::memcpy(image_.get(), image.data(), image.size());
    originalData_.assign(image.begin() + dataOffset, image.end());
    std::ranges::copy(catalog, catalog_.begin());
    write<std::uint8_t>(image_.get(), randomRva, 0xE9);
    write<std::uintptr_t>(image_.get(), randomRva + 1, reinterpret_cast<std::uintptr_t>(random) - (imageBase + randomRva + 5));
    write<std::uint8_t>(image_.get(), 0xC434F, 0xC3); // Return from the isolated hand-selection fragment.
    write<std::uint8_t>(image_.get(), 0xCEB96, 0xC3); // Return from the isolated target-selection fragment.
    // Growth's presentation-only hook: xor eax,eax; ret 20. The original arithmetic remains intact.
    constexpr auto presentationReturn = std::array<std::uint8_t, 5>{ 0x31, 0xC0, 0xC2, 0x14, 0x00 };
    std::ranges::copy(presentationReturn, image_.get() + 0xF0380);
    protect(image_.get(), 0x8000, PAGE_READONLY);
    protect(image_.get() + 0x8000, dataOffset - 0x8000, PAGE_EXECUTE_READ);
    if (!FlushInstructionCache(GetCurrentProcess(), image_.get(), dataOffset))
    {
        throw std::runtime_error("Cannot flush original code image");
    }
}

auto Engine::sameCatalog(std::span<const std::uint8_t> catalog) const noexcept -> bool
{
    return std::ranges::equal(catalog, catalog_);
}

void Engine::reset()
{
    std::memcpy(image_.get() + dataOffset, originalData_.data(), originalData_.size());
    game_.fill(0);
    attacker_.fill(0);
    defender_.fill(0);
    mode_ = 0;
    write<std::uintptr_t>(image_.get(), 0x2B63C4, reinterpret_cast<std::uintptr_t>(game_.data()));
    write<std::uintptr_t>(image_.get(), 0x2BB62C, reinterpret_cast<std::uintptr_t>(&mode_));
    write<std::uintptr_t>(image_.get(), 0x2B6364, reinterpret_cast<std::uintptr_t>(catalog_.data()));
    write<std::uint32_t>(image_.get(), 0x2FF4FC, 250);
    write<std::uint32_t>(image_.get(), 0x2FF568, 1);
    write<std::uint8_t>(image_.get(), playerRva + 0xE5, 2);
}

void Engine::battle(const BattleRequest& request, BattleResult& result)
{
    reset();
    seedBattle(attacker_.data(), request.attacker, request.attackerModifier, request.attackerProperty);
    seedBattle(defender_.data(), request.defender, request.defenderModifier, request.defenderProperty);
    using Battle = int(__stdcall*)(void*, void*);
    reinterpret_cast<Battle>(imageBase + 0xD31D0)(attacker_.data(), defender_.data());
    battleResult(attacker_.data(), result.attacker);
    battleResult(defender_.data(), result.defender);
}

void Engine::move(const MoveRequest& request, MoveResult& result)
{
    reset();
    result           = MoveResult{ -1, -1 };
    const auto board = std::span{ request.board }.first(request.boardSize);
    if (request.count == 0 || std::ranges::none_of(board, [](const auto& cell)
                                                   {
                                                       return cell.owner == 8;
                                                   }))
    {
        return;
    }

    auto* const player = image_.get() + playerRva;
    auto* const game   = game_.data();
    mode_              = request.boardSize == 25 ? 1 : 0;
    write<std::uint8_t>(player, 0xE5, mode_ + 2);
    write<std::uint8_t>(game, 0x178, static_cast<std::uint8_t>(request.owner));
    write<std::uint8_t>(game, 0x171, 1);
    write<std::uint8_t>(game, 0x16F, 0x22);
    write<std::uint8_t>(game, 0x17A, 255);
    write<std::uint8_t>(game, 0x179, 255);
    write<std::uint8_t>(player, 0x19F + request.owner * 0x30, static_cast<std::uint8_t>(request.count));
    write<std::uint8_t>(player, 0x19A + request.owner * 0x30, 1);
    for (auto index = std::size_t{}; index < 3; ++index)
    {
        write<std::uint8_t>(player, 0x184 + index + request.owner * 0x30, static_cast<std::uint8_t>(request.personality[index]));
    }

    for (auto index = std::size_t{}; index < request.boardSize; ++index)
    {
        const auto& cell = request.board[index];
        auto* const card = game + 0x344 + index * 0x9C;
        write<std::uint16_t>(card, 14, static_cast<std::uint16_t>(cell.model));
        write<std::uint8_t>(card, 13, static_cast<std::uint8_t>(cell.arrows));
        write<std::uint8_t>(card, 0x24, static_cast<std::uint8_t>(cell.property));
        write<std::uint8_t>(card, 0x28, static_cast<std::uint8_t>(cell.direction));
        for (auto offset = std::size_t{ 0x1C }; offset < 0x1F; ++offset)
        {
            write<std::uint8_t>(card, offset, static_cast<std::uint8_t>(cell.owner));
        }
    }

    for (auto index = std::size_t{}; index < request.count; ++index)
    {
        auto* const card = player + 0x20C + (request.owner * 5 + index) * 0x9C;
        write<std::uint16_t>(card, 14, static_cast<std::uint16_t>(request.hand[index].model));
        write<std::uint8_t>(card, 13, static_cast<std::uint8_t>(request.hand[index].arrows));
    }

    selectHand(request.owner, game);
    result.hand      = read<std::uint8_t>(game, 0x17A);
    using Placement  = int(__thiscall*)(void*);
    const auto ready = reinterpret_cast<Placement>(imageBase + 0xD68D0)(game);
    if (ready)
    {
        result.field = read<std::uint8_t>(game, 0x179);
    }
}

void Engine::growth(const GrowthRequest& request, GrowthResult& result)
{
    reset();
    auto baseline = std::array<std::uint8_t, 12>{};
    baseline[0]   = static_cast<std::uint8_t>(request.baseline[0]);
    baseline[2]   = static_cast<std::uint8_t>(request.baseline[1]);
    baseline[3]   = static_cast<std::uint8_t>(request.baseline[2]);
    baseline[4]   = static_cast<std::uint8_t>(request.baseline[3]);
    // Model is used only for the catalogue lookup. A private row zero preserves
    // the supplied baseline API without mutating the shared ordinary-AI catalogue.
    write<std::uintptr_t>(image_.get(), 0x2B6364, reinterpret_cast<std::uintptr_t>(baseline.data()));
    write<std::uintptr_t>(image_.get(), 0x2B6368, reinterpret_cast<std::uintptr_t>(&request.baseline[4]));
    for (auto index = std::size_t{ 1 }; index < 7; ++index)
    {
        write<std::uint8_t>(attacker_.data(), 7 + index, static_cast<std::uint8_t>(request.card[index]));
    }
    write<std::uint8_t>(attacker_.data(), 0x16, static_cast<std::uint8_t>(request.card[7]));
    using Growth = void(__thiscall*)(void*, std::uint32_t, std::uint32_t, void*);
    reinterpret_cast<Growth>(imageBase + 0xB3630)(attacker_.data(), request.mode, request.random, game_.data());
    std::ranges::copy(request.card, result.card);
    result.changed = -1;
    for (auto index = std::size_t{ 1 }; index < 7; ++index)
    {
        result.card[index] = read<std::uint8_t>(attacker_.data(), 7 + index);
        if (index <= 4 && result.card[index] != request.card[index])
        {
            result.changed = static_cast<std::int32_t>(index);
        }
    }
    result.sale = read<std::uint32_t>(attacker_.data(), 0x10);
}

void Engine::target(const TargetRequest& request, TargetResult& result)
{
    reset();
    result.field = -1;
    if (request.count == 0)
    {
        return;
    }
    if (request.count == 1)
    {
        result.field = static_cast<std::int32_t>(request.candidates[0]);
        return;
    }
    write<std::uint8_t>(image_.get(), playerRva + 0x19A, 1);
    write<std::uint8_t>(game_.data(), 0x171, 1);
    write<std::uint8_t>(game_.data(), 0x17A, 255);
    write<std::uint8_t>(game_.data(), 0x17D, static_cast<std::uint8_t>(request.count));
    for (auto index = std::size_t{}; index < request.count; ++index)
    {
        write<std::uint8_t>(game_.data(), 0x1DC + index, static_cast<std::uint8_t>(request.candidates[index]));
    }
    selectTarget(game_.data());
    result.field = read<std::uint8_t>(game_.data(), 0x17A);
}

auto valid(const BattleRequest& request) noexcept -> bool
{
    return std::ranges::all_of(request.attacker, [](auto value)
                               {
                                   return value <= 255;
                               }) &&
           std::ranges::all_of(request.defender, [](auto value)
                               {
                                   return value <= 255;
                               }) &&
           request.attacker[1] <= 3 && request.defender[1] <= 3 && request.attackerModifier >= -128 && request.attackerModifier <= 127 && request.defenderModifier >= -128 && request.defenderModifier <= 127 && request.attackerProperty <= 255 && request.defenderProperty <= 255;
}

auto valid(const MoveRequest& request) noexcept -> bool
{
    const auto players = request.boardSize == 16 ? 2U : request.boardSize == 25 ? 3U
                                                                                : 0U;
    if (request.count > 5 || request.owner >= players || std::ranges::any_of(request.personality, [](auto value)
                                                                             {
                                                                                 return value > 255;
                                                                             }))
    {
        return false;
    }

    for (auto index = std::size_t{}; index < request.count; ++index)
    {
        if (request.hand[index].model >= 250 || request.hand[index].arrows > 255)
        {
            return false;
        }
    }

    return std::ranges::all_of(std::span{ request.board }.first(request.boardSize), [players](const auto& cell)
                               {
                                   return cell.arrows <= 255 && cell.property <= 255 && cell.direction <= 7 &&
                                          ((cell.model < 250 && cell.owner < players) ||
                                           (cell.model == 0xFFFF && cell.owner == 8) ||
                                           (cell.model >= 0x8000 && cell.model <= 0x800B &&
                                            (cell.owner == 4 || cell.owner == 8 || cell.owner == 9)));
                               });
}

auto valid(const GrowthRequest& request) noexcept -> bool
{
    return request.card[0] < 65535 && request.card[2] <= 3 &&
           std::ranges::all_of(std::span{ request.card }.subspan(1), [](auto value)
                               {
                                   return value <= 255;
                               }) &&
           std::ranges::all_of(std::span{ request.baseline }.first(3), [](auto value)
                               {
                                   return value >= 1 && value <= 255;
                               }) &&
           request.baseline[3] <= 255 && request.baseline[4] <= 0x7FFFFFFF && request.mode <= 255 && request.random <= 255 && (request.precision == 53 || request.precision == 64);
}

auto valid(const TargetRequest& request) noexcept -> bool
{
    if (request.count > 8 || (request.boardSize != 16 && request.boardSize != 25))
    {
        return false;
    }
    for (auto index = std::size_t{}; index < request.count; ++index)
    {
        if (request.candidates[index] >= request.boardSize)
        {
            return false;
        }
        for (auto previous = std::size_t{}; previous < index; ++previous)
        {
            if (request.candidates[index] == request.candidates[previous])
            {
                return false;
            }
        }
    }
    return true;
}

} // namespace tmserver::original
