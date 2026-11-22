#include "engine.h"
#include <Windows.h>
#include <atomic>

namespace
{

constexpr auto randomFailure = DWORD{ 0xE0010001 };
constexpr auto imageSize     = std::uint32_t{ 0x35B000 };
auto           engine        = std::unique_ptr<tmserver::original::Engine>{};
auto           references    = std::uint32_t{};
auto           gate          = std::atomic_flag{};
auto           randomSource  = tmserver::original::Random{};
void*          randomUser    = nullptr;
auto           maximumDraw   = std::uint32_t{};

auto acquireGate() noexcept -> std::unique_ptr<std::atomic_flag, void (*)(std::atomic_flag*)>
{
    auto* acquired = static_cast<std::atomic_flag*>(nullptr);
    if (!gate.test_and_set(std::memory_order_acquire))
    {
        acquired = &gate;
    }

    return { acquired, [](std::atomic_flag* flag)
             {
                 flag->clear(std::memory_order_release);
             } };
}

auto __cdecl originalRandom() -> int
{
    auto value = std::uint32_t{};
    if (randomSource(randomUser, &value) != 0 || value > maximumDraw)
    {
        RaiseException(randomFailure, 0, 0, nullptr);
    }

    return static_cast<int>(value);
}

auto guardedBattle(const tmserver::original::BattleRequest* request, tmserver::original::BattleResult* result) noexcept -> std::uint32_t
{
    __try
    {
        engine->battle(*request, *result);
        return 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return GetExceptionCode();
    }
}

auto guardedMove(const tmserver::original::MoveRequest* request, tmserver::original::MoveResult* result) noexcept -> std::uint32_t
{
    __try
    {
        engine->move(*request, *result);
        return 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return GetExceptionCode();
    }
}

auto guardedGrowth(const tmserver::original::GrowthRequest* request, tmserver::original::GrowthResult* result) noexcept -> std::uint32_t
{
    // Save registers as well as the control word: an exception can interrupt
    // the original helper before it has emptied its floating-point stack.
    auto       floatingState = std::array<std::uint8_t, 108>{};
    const auto control = request->precision == 24 ? std::uint16_t{ 0x007F } : request->precision == 53 ? std::uint16_t{ 0x027F } : std::uint16_t{ 0x037F };
    __asm
    {
        fnsave floatingState
    }
    __try
    {
        __try
        {
            __asm
                {
                fldcw control
                }
            engine->growth(*request, *result);
            return 0;
        }
        __finally
        {
            __asm
            {
                frstor floatingState
            }
        }
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return GetExceptionCode();
    }
}

auto guardedTarget(const tmserver::original::TargetRequest* request, tmserver::original::TargetResult* result) noexcept -> std::uint32_t
{
    __try
    {
        engine->target(*request, *result);
        return 0;
    }
    __except (EXCEPTION_EXECUTE_HANDLER)
    {
        return GetExceptionCode();
    }
}

} // namespace

auto __cdecl tm_rules_abi() noexcept -> std::uint32_t
{
    static_assert(sizeof(void*) == 4);
    static_assert(sizeof(tmserver::original::BattleRequest) == 48);
    static_assert(sizeof(tmserver::original::BattleResult) == 32);
    static_assert(sizeof(tmserver::original::MoveRequest) == 564);
    static_assert(sizeof(tmserver::original::MoveResult) == 8);
    static_assert(sizeof(tmserver::original::GrowthRequest) == 64);
    static_assert(sizeof(tmserver::original::GrowthResult) == 40);
    static_assert(sizeof(tmserver::original::TargetRequest) == 40);
    static_assert(sizeof(tmserver::original::TargetResult) == 4);
    return 3;
}

auto __cdecl tm_rules_open(const std::uint8_t* image, std::uint32_t size, const std::uint8_t* catalog, std::uint32_t catalogSize) noexcept -> std::uint32_t
{
    const auto lock = acquireGate();
    if (!lock)
    {
        return 1;
    }

    if (!image || size != imageSize || !catalog || catalogSize != 3000 || references >= 65535)
    {
        return 2;
    }

    try
    {
        const auto rows = std::span{ catalog, catalogSize };
        if (engine && !engine->sameCatalog(rows))
        {
            return 2;
        }

        if (!engine)
        {
            engine = std::make_unique<tmserver::original::Engine>(std::span{ image, size }, rows, originalRandom);
        }

        ++references;
        return 0;
    }
    catch (...)
    {
        return 4;
    }
}

auto __cdecl tm_rules_close() noexcept -> std::uint32_t
{
    const auto lock = acquireGate();
    if (!lock)
    {
        return 1;
    }

    if (!engine)
    {
        return 3;
    }

    if (--references == 0)
    {
        engine.reset();
    }

    return 0;
}

auto __cdecl tm_rules_battle(const tmserver::original::BattleRequest* request, tmserver::original::Random random, void* user, tmserver::original::BattleResult* result) noexcept -> std::uint32_t
{
    const auto lock = acquireGate();
    if (!lock)
    {
        return 1;
    }

    if (!engine)
    {
        return 3;
    }

    if (!request || !result || !random || !tmserver::original::valid(*request))
    {
        return 2;
    }

    randomSource      = random;
    randomUser        = user;
    maximumDraw       = 0x7FFFFFFF;
    const auto status = guardedBattle(request, result);
    randomSource      = nullptr;
    randomUser        = nullptr;
    return status;
}

auto __cdecl tm_rules_move(const tmserver::original::MoveRequest* request, tmserver::original::Random random, void* user, tmserver::original::MoveResult* result) noexcept -> std::uint32_t
{
    const auto lock = acquireGate();
    if (!lock)
    {
        return 1;
    }

    if (!engine)
    {
        return 3;
    }

    if (!request || !result || !random || !tmserver::original::valid(*request))
    {
        return 2;
    }

    randomSource      = random;
    randomUser        = user;
    maximumDraw       = 32767;
    const auto status = guardedMove(request, result);
    randomSource      = nullptr;
    randomUser        = nullptr;
    return status;
}

auto __cdecl tm_rules_growth(const tmserver::original::GrowthRequest* request, tmserver::original::GrowthResult* result) noexcept -> std::uint32_t
{
    const auto lock = acquireGate();
    if (!lock)
    {
        return 1;
    }
    if (!engine)
    {
        return 3;
    }
    if (!request || !result || !tmserver::original::valid(*request))
    {
        return 2;
    }
    return guardedGrowth(request, result);
}

auto __cdecl tm_rules_target(const tmserver::original::TargetRequest* request, tmserver::original::Random random, void* user, tmserver::original::TargetResult* result) noexcept -> std::uint32_t
{
    const auto lock = acquireGate();
    if (!lock)
    {
        return 1;
    }
    if (!engine)
    {
        return 3;
    }
    if (!request || !result || !random || !tmserver::original::valid(*request))
    {
        return 2;
    }
    randomSource      = random;
    randomUser        = user;
    maximumDraw       = 32767;
    const auto status = guardedTarget(request, result);
    randomSource      = nullptr;
    randomUser        = nullptr;
    return status;
}
