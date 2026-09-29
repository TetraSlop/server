#pragma once

#include "api.h"
#include <array>
#include <memory>
#include <span>
#include <vector>

namespace tmserver::original
{

using RandomThunk = int(__cdecl*)();

struct ReleaseMapping
{
    void operator()(std::uint8_t* mapping) const noexcept;
};

class Engine final
{
public:
    Engine(std::span<const std::uint8_t> image, std::span<const std::uint8_t> catalog, RandomThunk random);
    auto sameCatalog(std::span<const std::uint8_t> catalog) const noexcept -> bool;
    void battle(const BattleRequest& request, BattleResult& result);
    void move(const MoveRequest& request, MoveResult& result);
    void growth(const GrowthRequest& request, GrowthResult& result);
    void target(const TargetRequest& request, TargetResult& result);

private:
    void reset();

    std::unique_ptr<std::uint8_t, ReleaseMapping> image_;
    std::vector<std::uint8_t>                     originalData_;
    std::array<std::uint8_t, 3000>                catalog_{};
    std::array<std::uint8_t, 0x4000>              game_{};
    std::array<std::uint8_t, 0x9C>                attacker_{};
    std::array<std::uint8_t, 0x9C>                defender_{};
    std::uint8_t                                  mode_{};
};

auto valid(const BattleRequest& request) noexcept -> bool;
auto valid(const MoveRequest& request) noexcept -> bool;
auto valid(const GrowthRequest& request) noexcept -> bool;
auto valid(const TargetRequest& request) noexcept -> bool;

} // namespace tmserver::original
