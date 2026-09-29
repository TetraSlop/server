#pragma once

#include <cstdint>

namespace tmserver::original
{

using Random = std::uint32_t(__cdecl*)(void*, std::uint32_t*);

struct BattleRequest
{
    std::uint32_t attacker[4]; // Attack, type, physical and magical.
    std::uint32_t defender[4];
    std::int32_t  attackerModifier;
    std::int32_t  defenderModifier;
    std::uint32_t attackerProperty;
    std::uint32_t defenderProperty;
};

struct BattleResult
{
    std::uint32_t attacker[4]; // Start, final, selector and multiplier.
    std::uint32_t defender[4];
};

struct HandCard
{
    std::uint32_t model;
    std::uint32_t arrows;
};

struct BoardCell
{
    std::uint32_t model;
    std::uint32_t arrows;
    std::uint32_t owner;
};

struct MoveRequest
{
    std::uint32_t count;
    std::uint32_t owner;
    std::uint32_t personality[3];
    HandCard      hand[5];
    BoardCell     board[16];
};

struct MoveResult
{
    std::int32_t hand;
    std::int32_t field;
};

struct GrowthRequest
{
    std::uint32_t card[8];     // Model, attack, type, physical, magical, points, arrows and deck.
    std::uint32_t baseline[5]; // Attack, physical, magical, points and price.
    std::uint32_t mode;
    std::uint32_t random;
    std::uint32_t precision;
};

struct GrowthResult
{
    std::uint32_t card[8];
    std::uint32_t sale;
    std::int32_t  changed; // Card array index, or -1 when no attack/type/defense changed.
};

struct TargetRequest
{
    std::uint32_t count;
    std::uint32_t boardSize;
    std::uint32_t candidates[8];
};

struct TargetResult
{
    std::int32_t field;
};

} // namespace tmserver::original

extern "C"
{
    auto __cdecl tm_rules_abi() noexcept -> std::uint32_t;
    auto __cdecl tm_rules_open(const std::uint8_t* image, std::uint32_t imageSize, const std::uint8_t* catalog, std::uint32_t catalogSize) noexcept -> std::uint32_t;
    auto __cdecl tm_rules_close() noexcept -> std::uint32_t;
    auto __cdecl tm_rules_battle(const tmserver::original::BattleRequest* request, tmserver::original::Random random, void* user, tmserver::original::BattleResult* result) noexcept -> std::uint32_t;
    auto __cdecl tm_rules_move(const tmserver::original::MoveRequest* request, tmserver::original::Random random, void* user, tmserver::original::MoveResult* result) noexcept -> std::uint32_t;
    auto __cdecl tm_rules_growth(const tmserver::original::GrowthRequest* request, tmserver::original::GrowthResult* result) noexcept -> std::uint32_t;
    auto __cdecl tm_rules_target(const tmserver::original::TargetRequest* request, tmserver::original::Random random, void* user, tmserver::original::TargetResult* result) noexcept -> std::uint32_t;
}
