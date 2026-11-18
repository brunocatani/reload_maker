#pragma once

// How closely one source's convex hulls follow its visual mesh, in game units
// of the solver frame. Plain C++; used to compare decomposition algorithms.
#include <array>
#include <cstdint>
#include <vector>

namespace reloadmaker::collision_quality
{
    struct Mesh
    {
        std::vector<std::array<double, 3>> vertices;
        std::vector<std::array<std::int32_t, 3>> triangles;
    };

    struct Hull
    {
        std::vector<std::array<double, 3>> points;
        std::vector<std::array<std::uint32_t, 3>> faces;
    };

    struct Quality
    {
        int meshSamples{};
        int hullSamples{};
        // Visual surface outside every hull: what a hand passes through.
        double uncoveredFraction{};  // share of samples more than kUncoveredTolerance outside
        double uncoveredP95{};
        double uncoveredMax{};
        // Hull surface outside and away from the visual surface: filled gaps
        // and bulges. Hull surface inside the solid (behind the nearest
        // triangle) or inside a neighbouring hull does not count.
        double overfillP95{};
        double overfillMax{};
    };

    inline constexpr double kUncoveredTolerance = .25;

    [[nodiscard]] Quality measure(const Mesh& mesh, const std::vector<Hull>& hulls);
}
