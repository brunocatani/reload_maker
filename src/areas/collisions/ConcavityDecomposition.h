#pragma once

// Concavity-driven convex decomposition of one weapon source with an
// error-bounded hull fit: the candidate replacement for ROCK's gap partition.
// Plain C++ over ROCK's own geometry types and synchronous, so ROCK's
// collision worker thread can run it unchanged; Maker measures it.
//
// 1. Welded islands are clustered: islands nearer than the preserved gap
//    always share a piece, further ones merge nearest first only while the
//    source exceeds maxPieces (ROCK joins every island above eight).
// 2. Each piece's hull is compared with the piece's own surface outside it.
//    While the source has fewer than maxPieces pieces, the piece with the
//    most bulge over the tolerance is cut by the candidate plane that removes
//    the most total bulge; a cut must remove a tenth of it, confirmed by a
//    full re-measurement, and leave no sliver. Gaps (trigger guards, bipod
//    legs) are bulge, so they are opened where it pays most.
// 3. Every hull is a greedy quickhull: the farthest outside point is added
//    until no input vertex lies more than fineTolerance outside or the hull
//    has targetHullPoints, and beyond that only while a vertex lies more than
//    hullTolerance outside, so no hull misses its surface by more than that
//    in any direction (ROCK's support fit checks a fixed direction set).
#include "physics-interaction/weapon/collision/WeaponGapDecomposition.h"

#include <array>
#include <atomic>
#include <cstdint>
#include <span>
#include <string>
#include <vector>

namespace reloadmaker::concavity_decomposition
{
    using Point = rock::weapon_gap_decomposition::Point;
    using Triangle = rock::weapon_gap_decomposition::Triangle;

    struct Settings
    {
        double gap{ 1 };            // ROCK's minimum preserved gap (game units)
        double weld{ .01 };         // ROCK's island weld grid
        double dedup{ .14 };        // vertex merge before hull fitting
        // ROCK's hull budget: refine to fineTolerance within targetHullPoints,
        // then past it, up to maxHullPoints, only while beyond hullTolerance.
        double fineTolerance{ .05 };
        double hullTolerance{ .5 }; // largest surface miss allowed per hull
        double concavity{ .5 };     // bulge a piece may keep without a cut
        std::size_t maxPieces{ rock::weapon_gap_decomposition::kMaxChildren };
        std::size_t targetHullPoints{ 96 };
        std::size_t maxHullPoints{ 252 };
    };

    struct Hull
    {
        std::vector<Point> points;
        std::vector<std::array<std::uint32_t, 3>> faces; // outward, counter-clockwise
    };

    struct Result
    {
        std::vector<Hull> pieces;
        std::size_t concavityCuts{};
        double worstConcavity{}; // largest remaining bulge, game units
        bool budgetLimited{};    // maxPieces reached with bulge above tolerance
        std::string error;
    };

    // A source whose bounding box is under this in every direction (screw
    // heads, rivets, small plates: SCAR-H parts measure 0.3-0.9 gu) offers a
    // hand nothing to touch, and would cost a whole body; it gets no
    // collider. The smallest control kept on the test weapons is 1.4 gu.
    inline constexpr double kMinSourceExtent = 1.0;
    [[nodiscard]] bool negligible(std::span<const Triangle> triangles, double minExtent = kMinSourceExtent);

    // Blocking; checks cancel between cuts.
    [[nodiscard]] Result decompose(std::span<const Triangle> triangles, const Settings& settings, const std::atomic<bool>* cancel);

    // The greedy quickhull alone. Empty when the points span no volume.
    [[nodiscard]] Hull fitHull(std::span<const Point> points, const Settings& settings);
}
