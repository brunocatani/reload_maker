#include "areas/collisions/ConcavityDecomposition.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <unordered_map>
#include <unordered_set>
#include <cstdio>

namespace reloadmaker::concavity_decomposition
{
    namespace
    {
        namespace gap = rock::weapon_gap_decomposition;
        using gap::cross;
        using gap::dot;

        double length(Point p) { return std::sqrt(dot(p, p)); }
        Point unit(Point p)
        {
            const double l = length(p);
            return l > 1e-300 ? p * (1.0 / l) : Point{};
        }

        template <class Coroutine>
        void finish(Coroutine&& task)
        {
            while (task.step()) {}
        }

        constexpr double kInfinity = std::numeric_limits<double>::infinity();
        // Candidate scoring samples; the accepted pieces are measured densely.
        // Candidate cuts are ranked on a coarse hull with few samples; the
        // winner is refitted and measured in full (sweep over six weapons:
        // 3-4x faster than full scoring at equal fidelity).
        constexpr int kScoreSamples = 32;
        constexpr std::size_t kScoreHullPoints = 32;
        constexpr double kScoreFineTolerance = .15;
        constexpr int kMeasureSamples = 160;
        constexpr int kOffsetsPerNormal = 7;

        // Deterministic low-discrepancy sequence: identical runs, identical cuts.
        double halton(int index, int base)
        {
            double f = 1, r = 0;
            for (int i = index + 1; i > 0; i /= base) {
                f /= base;
                r += f * (i % base);
            }
            return r;
        }

        // ---- greedy quickhull ------------------------------------------------

        struct Face
        {
            std::array<std::uint32_t, 3> v{};
            Point normal{};
            double offset{};
            std::vector<std::uint32_t> outside;
            std::uint32_t farthest{};
            double farthestDistance{ -kInfinity };
            bool alive{ true };
        };

        class QuickHull
        {
        public:
            QuickHull(std::span<const Point> points, const Settings& s)
                : _points(points),
                  _fine((std::max)(0.0, s.fineTolerance)),
                  _tolerance((std::max)(_fine, s.hullTolerance)),
                  _target((std::max<std::size_t>)(4, s.targetHullPoints)),
                  _maxPoints((std::max)(_target, s.maxHullPoints))
            {}

            Hull run()
            {
                if (_points.size() < 4 || !seed()) {
                    return {};
                }
                std::size_t vertices = 4;
                std::vector<std::size_t> visible;
                std::unordered_set<std::uint64_t> edges;
                std::vector<std::uint32_t> orphans;
                while (vertices < _maxPoints) {
                    std::size_t best = _faces.size();
                    double bestDistance = vertices < _target ? _fine : _tolerance;
                    for (std::size_t f = 0; f < _faces.size(); ++f) {
                        if (_faces[f].alive && _faces[f].farthestDistance > bestDistance) {
                            bestDistance = _faces[f].farthestDistance;
                            best = f;
                        }
                    }
                    if (best == _faces.size()) {
                        break;
                    }
                    const auto apex = _faces[best].farthest;
                    const Point p = _points[apex];
                    visible.clear();
                    edges.clear();
                    for (std::size_t f = 0; f < _faces.size(); ++f) {
                        if (_faces[f].alive && dot(_faces[f].normal, p) - _faces[f].offset > _epsilon) {
                            visible.push_back(f);
                            for (int e = 0; e < 3; ++e) {
                                edges.insert(key(_faces[f].v[e], _faces[f].v[(e + 1) % 3]));
                            }
                        }
                    }
                    orphans.clear();
                    for (const auto f : visible) {
                        _faces[f].alive = false;
                        for (const auto o : _faces[f].outside) {
                            if (o != apex) {
                                orphans.push_back(o);
                            }
                        }
                        std::vector<std::uint32_t>().swap(_faces[f].outside);
                    }
                    const auto firstNew = _faces.size();
                    for (const auto f : visible) {
                        const auto v = _faces[f].v;
                        for (int e = 0; e < 3; ++e) {
                            const auto a = v[e], b = v[(e + 1) % 3];
                            if (!edges.contains(key(b, a))) {
                                addFace(a, b, apex);
                            }
                        }
                    }
                    assign(orphans, firstNew);
                    ++vertices;
                }
                return collect();
            }

        private:
            static std::uint64_t key(std::uint32_t a, std::uint32_t b) { return (std::uint64_t(a) << 32) | b; }

            bool seed()
            {
                Point lo{ kInfinity, kInfinity, kInfinity }, hi = lo * -1.0;
                for (const auto& p : _points) {
                    lo = { (std::min)(lo.x, p.x), (std::min)(lo.y, p.y), (std::min)(lo.z, p.z) };
                    hi = { (std::max)(hi.x, p.x), (std::max)(hi.y, p.y), (std::max)(hi.z, p.z) };
                }
                const double scale = length(hi - lo);
                if (!(scale > 0)) {
                    return false;
                }
                _epsilon = scale * 1e-9;
                // Two farthest axis extremes, then farthest from their line and plane.
                std::array<std::uint32_t, 6> extremes{};
                for (std::uint32_t i = 0; i < _points.size(); ++i) {
                    for (int a = 0; a < 3; ++a) {
                        if (gap::axis(_points[i], a) < gap::axis(_points[extremes[a * 2]], a)) extremes[a * 2] = i;
                        if (gap::axis(_points[i], a) > gap::axis(_points[extremes[a * 2 + 1]], a)) extremes[a * 2 + 1] = i;
                    }
                }
                std::uint32_t i0 = 0, i1 = 0;
                double far = -1;
                for (auto a : extremes) {
                    for (auto b : extremes) {
                        const double d = dot(_points[a] - _points[b], _points[a] - _points[b]);
                        if (d > far) { far = d; i0 = a; i1 = b; }
                    }
                }
                const Point line = unit(_points[i1] - _points[i0]);
                std::uint32_t i2 = i0;
                far = -1;
                for (std::uint32_t i = 0; i < _points.size(); ++i) {
                    const Point d = _points[i] - _points[i0];
                    const Point off = d - line * dot(d, line);
                    if (dot(off, off) > far) { far = dot(off, off); i2 = i; }
                }
                if (std::sqrt(far) < scale * 1e-6) {
                    return false;
                }
                const Point plane = unit(cross(_points[i1] - _points[i0], _points[i2] - _points[i0]));
                std::uint32_t i3 = i0;
                far = -1;
                for (std::uint32_t i = 0; i < _points.size(); ++i) {
                    const double d = std::abs(dot(plane, _points[i] - _points[i0]));
                    if (d > far) { far = d; i3 = i; }
                }
                if (far < scale * 1e-6) {
                    return false;
                }
                _interior = (_points[i0] + _points[i1] + _points[i2] + _points[i3]) * .25;
                addFace(i0, i1, i2);
                addFace(i0, i1, i3);
                addFace(i0, i2, i3);
                addFace(i1, i2, i3);
                std::vector<std::uint32_t> rest;
                rest.reserve(_points.size());
                for (std::uint32_t i = 0; i < _points.size(); ++i) {
                    if (i != i0 && i != i1 && i != i2 && i != i3) rest.push_back(i);
                }
                assign(rest, 0);
                return true;
            }

            // Outward by the interior point: the seed's centroid stays inside
            // every later hull because the hull only grows.
            void addFace(std::uint32_t a, std::uint32_t b, std::uint32_t c)
            {
                Face face;
                face.v = { a, b, c };
                face.normal = unit(cross(_points[b] - _points[a], _points[c] - _points[a]));
                face.offset = dot(face.normal, _points[a]);
                if (dot(face.normal, _interior) - face.offset > 0) {
                    face.v = { a, c, b };
                    face.normal = face.normal * -1.0;
                    face.offset = -face.offset;
                }
                _faces.push_back(std::move(face));
            }

            void assign(const std::vector<std::uint32_t>& points, std::size_t firstFace)
            {
                for (const auto i : points) {
                    std::size_t owner = _faces.size();
                    double best = _epsilon;
                    for (std::size_t f = firstFace; f < _faces.size(); ++f) {
                        if (!_faces[f].alive) continue;
                        const double d = dot(_faces[f].normal, _points[i]) - _faces[f].offset;
                        if (d > best) { best = d; owner = f; }
                    }
                    if (owner == _faces.size()) continue;
                    auto& face = _faces[owner];
                    face.outside.push_back(i);
                    if (best > face.farthestDistance) {
                        face.farthestDistance = best;
                        face.farthest = i;
                    }
                }
            }

            Hull collect() const
            {
                Hull hull;
                std::unordered_map<std::uint32_t, std::uint32_t> remap;
                for (const auto& face : _faces) {
                    if (!face.alive) continue;
                    std::array<std::uint32_t, 3> out{};
                    for (int i = 0; i < 3; ++i) {
                        auto [it, inserted] = remap.emplace(face.v[i], std::uint32_t(hull.points.size()));
                        if (inserted) hull.points.push_back(_points[face.v[i]]);
                        out[i] = it->second;
                    }
                    hull.faces.push_back(out);
                }
                return hull;
            }

            std::span<const Point> _points;
            double _fine;
            double _tolerance;
            std::size_t _target;
            std::size_t _maxPoints;
            double _epsilon{};
            Point _interior{};
            std::vector<Face> _faces;
        };

        // ---- nearest-surface queries -----------------------------------------

        Point closestOnTriangle(Point p, const Triangle& t)
        {
            // Ericson, Real-Time Collision Detection 5.1.5.
            const Point ab = t[1] - t[0], ac = t[2] - t[0], ap = p - t[0];
            const double d1 = dot(ab, ap), d2 = dot(ac, ap);
            if (d1 <= 0 && d2 <= 0) return t[0];
            const Point bp = p - t[1];
            const double d3 = dot(ab, bp), d4 = dot(ac, bp);
            if (d3 >= 0 && d4 <= d3) return t[1];
            const double vc = d1 * d4 - d3 * d2;
            if (vc <= 0 && d1 >= 0 && d3 <= 0) return t[0] + ab * (d1 / (d1 - d3));
            const Point cp = p - t[2];
            const double d5 = dot(ab, cp), d6 = dot(ac, cp);
            if (d6 >= 0 && d5 <= d6) return t[2];
            const double vb = d5 * d2 - d1 * d6;
            if (vb <= 0 && d2 >= 0 && d6 <= 0) return t[0] + ac * (d2 / (d2 - d6));
            const double va = d3 * d6 - d5 * d4;
            if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) {
                return t[1] + (t[2] - t[1]) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)));
            }
            const double denominator = 1 / (va + vb + vc);
            return t[0] + ab * (vb * denominator) + ac * (vc * denominator);
        }

        // Median-split bounding volume tree over one piece's triangles.
        class NearestTree
        {
        public:
            explicit NearestTree(const std::vector<Triangle>& mesh) : _mesh(mesh), _order(mesh.size())
            {
                std::iota(_order.begin(), _order.end(), 0u);
                if (!mesh.empty()) {
                    _nodes.reserve(mesh.size() / 2 + 1);
                    build(0, std::uint32_t(mesh.size()));
                }
            }

            // Distance from p to the surface when p lies outside it, else 0.
            // Inside is the back of the nearest triangle: hull surface inside
            // the solid (the caps a cut leaves) is not a bulge.
            double outsideDistance(Point p) const
            {
                double best = kInfinity;
                if (_nodes.empty()) return best;
                Point closest{};
                std::uint32_t owner = 0;
                std::uint32_t stack[64];
                int top = 0;
                stack[top++] = 0;
                while (top > 0) {
                    const auto& node = _nodes[stack[--top]];
                    if (boxDistanceSquared(node, p) >= best * best) continue;
                    if (node.count) {
                        for (std::uint32_t i = node.first; i < node.first + node.count; ++i) {
                            const Point q = closestOnTriangle(p, _mesh[_order[i]]);
                            const double d = length(p - q);
                            if (d < best) { best = d; closest = q; owner = _order[i]; }
                        }
                        continue;
                    }
                    // Nearer child last, so it is searched first.
                    const auto l = node.left, r = node.right;
                    const bool leftNearer = boxDistanceSquared(_nodes[l], p) < boxDistanceSquared(_nodes[r], p);
                    if (top + 2 > 64) continue;
                    stack[top++] = leftNearer ? r : l;
                    stack[top++] = leftNearer ? l : r;
                }
                if (best < kInfinity && dot(p - closest, gap::normal(_mesh[owner])) < 0) return 0;
                return best;
            }

        private:
            struct Node
            {
                Point lo, hi;
                std::uint32_t first{}, count{}, left{}, right{};
            };

            static double boxDistanceSquared(const Node& n, Point p)
            {
                const double dx = (std::max)({ n.lo.x - p.x, 0.0, p.x - n.hi.x });
                const double dy = (std::max)({ n.lo.y - p.y, 0.0, p.y - n.hi.y });
                const double dz = (std::max)({ n.lo.z - p.z, 0.0, p.z - n.hi.z });
                return dx * dx + dy * dy + dz * dz;
            }

            std::uint32_t build(std::uint32_t first, std::uint32_t count)
            {
                const auto index = std::uint32_t(_nodes.size());
                _nodes.emplace_back();
                Point lo{ kInfinity, kInfinity, kInfinity }, hi = lo * -1.0;
                for (std::uint32_t i = first; i < first + count; ++i) {
                    for (const auto& p : _mesh[_order[i]]) {
                        lo = { (std::min)(lo.x, p.x), (std::min)(lo.y, p.y), (std::min)(lo.z, p.z) };
                        hi = { (std::max)(hi.x, p.x), (std::max)(hi.y, p.y), (std::max)(hi.z, p.z) };
                    }
                }
                _nodes[index].lo = lo;
                _nodes[index].hi = hi;
                if (count <= 4) {
                    _nodes[index].first = first;
                    _nodes[index].count = count;
                    return index;
                }
                const Point span = hi - lo;
                const int a = span.x >= span.y && span.x >= span.z ? 0 : span.y >= span.z ? 1 : 2;
                const auto mid = first + count / 2;
                std::nth_element(_order.begin() + first, _order.begin() + mid, _order.begin() + first + count,
                    [&](auto i, auto j) { return gap::axis(gap::center(_mesh[i]), a) < gap::axis(gap::center(_mesh[j]), a); });
                const auto left = build(first, mid - first);
                const auto right = build(mid, first + count - mid);
                _nodes[index].left = left;
                _nodes[index].right = right;
                return index;
            }

            const std::vector<Triangle>& _mesh;
            std::vector<std::uint32_t> _order;
            std::vector<Node> _nodes;
        };

        // ---- pieces ----------------------------------------------------------

        struct Plane
        {
            Point normal;
            double offset;
        };

        struct Piece
        {
            std::vector<Triangle> mesh;
            std::vector<Plane> cuts; // planes this piece was cut by: its open caps
            std::vector<Point> points; // deduplicated mesh vertices
            Hull hull;
            double concavity{}; // largest bulge: decides whether the piece is done
            double bulge{};     // bulge integrated over the hull surface: ranks cuts
            Point deepest{};    // hull sample with the largest bulge
            bool final{};       // no cut improves it
        };

        std::vector<Point> vertices(const std::vector<Triangle>& mesh, double grid)
        {
            std::unordered_set<std::array<std::int64_t, 3>, gap::detail::KeyHash> seen;
            std::vector<Point> out;
            out.reserve(mesh.size());
            for (const auto& t : mesh) {
                for (const auto& p : t) {
                    if (seen.insert({ std::llround(p.x / grid), std::llround(p.y / grid), std::llround(p.z / grid) }).second) out.push_back(p);
                }
            }
            return out;
        }

        struct Bulge
        {
            double max{};      // largest hull-to-surface distance
            double integral{}; // mean distance times hull area (game units^3)
            Point deepest{};
        };

        // Distance from the hull surface to the piece's own surface outside
        // it, skipping the caps its cut planes left open (not visual surface).
        // The maximum says whether a piece is done; the integral ranks cuts,
        // so removing one of several equally deep notches still counts.
        Bulge concavity(const Hull& hull, const NearestTree& tree, const std::vector<Plane>& cuts, int samples)
        {
            Bulge out;
            std::vector<double> cumulative;
            cumulative.reserve(hull.faces.size());
            double total = 0;
            for (const auto& f : hull.faces) {
                total += length(cross(hull.points[f[1]] - hull.points[f[0]], hull.points[f[2]] - hull.points[f[0]])) * .5;
                cumulative.push_back(total);
            }
            if (total <= 0) return out;
            double sum = 0;
            for (int i = 0; i < samples; ++i) {
                const double target = (i + halton(i, 2)) / samples * total;
                const auto face = std::min<std::size_t>(std::lower_bound(cumulative.begin(), cumulative.end(), target) - cumulative.begin(), hull.faces.size() - 1);
                double u = halton(i, 3), v = halton(i, 5);
                if (u + v > 1) { u = 1 - u; v = 1 - v; }
                const auto& f = hull.faces[face];
                const Point a = hull.points[f[0]];
                const Point p = a + (hull.points[f[1]] - a) * u + (hull.points[f[2]] - a) * v;
                bool cap = false;
                for (const auto& c : cuts) {
                    if (std::abs(dot(c.normal, p) - c.offset) < 1e-4) { cap = true; break; }
                }
                if (cap) continue;
                const double d = tree.outsideDistance(p);
                sum += d;
                if (d > out.max) {
                    out.max = d;
                    out.deepest = p;
                }
            }
            out.integral = sum / samples * total;
            return out;
        }

        void clipMesh(const std::vector<Triangle>& mesh, Plane plane, std::vector<Triangle>& below, std::vector<Triangle>& above)
        {
            below.clear();
            above.clear();
            for (const auto& t : mesh) {
                gap::detail::clip(t, plane.normal, plane.offset, below);
                gap::detail::clip(t, plane.normal * -1.0, -plane.offset, above);
            }
        }

        bool measure(Piece& piece, const Settings& s, int samples)
        {
            piece.points = vertices(piece.mesh, s.dedup);
            piece.hull = fitHull(piece.points, s);
            if (piece.hull.faces.empty()) {
                return false;
            }
            const NearestTree tree(piece.mesh);
            const auto b = concavity(piece.hull, tree, piece.cuts, samples);
            piece.concavity = b.max;
            piece.bulge = b.integral;
            piece.deepest = b.deepest;
            return true;
        }

        // Cut directions for one piece: the weapon axes and the two directions
        // across its deepest bulge, each with the piece's extent along it and
        // the offset through the bulge's nearest surface.
        struct Axis
        {
            Point normal;
            double lo, hi, through;
        };

        std::vector<Axis> axes(const Piece& piece)
        {
            std::vector<Point> normals{ { 1, 0, 0 }, { 0, 1, 0 }, { 0, 0, 1 } };
            const auto& d = piece.deepest;
            Point nearest = d;
            {
                double best = kInfinity;
                for (const auto& t : piece.mesh) {
                    const Point c = gap::center(t);
                    const double dd = dot(c - d, c - d);
                    if (dd < best) { best = dd; nearest = c; }
                }
            }
            const Point depth = unit(nearest - d);
            if (length(depth) > 0) {
                Point other = std::abs(depth.x) < .9 ? Point{ 1, 0, 0 } : Point{ 0, 1, 0 };
                const Point across = unit(cross(depth, other));
                normals.push_back(across);
                normals.push_back(unit(cross(depth, across)));
            }
            std::vector<Axis> out;
            for (const auto& n : normals) {
                double lo = kInfinity, hi = -kInfinity;
                for (const auto& p : piece.hull.points) {
                    lo = (std::min)(lo, dot(n, p));
                    hi = (std::max)(hi, dot(n, p));
                }
                if (hi - lo > 1e-6) out.push_back({ n, lo, hi, dot(n, nearest) });
            }
            return out;
        }

        // The point sets clipping would give each side, without clipping or
        // hashing: the piece's vertices by side, plus every mesh edge's
        // crossing of the plane (duplicates are harmless to the hull).
        void sides(const Piece& piece, Plane plane, std::vector<Point>& below, std::vector<Point>& above)
        {
            below.clear();
            above.clear();
            for (const auto& p : piece.points) {
                const double d = dot(plane.normal, p) - plane.offset;
                if (d <= 0) below.push_back(p);
                if (d >= 0) above.push_back(p);
            }
            for (const auto& t : piece.mesh) {
                const double d[3]{ dot(plane.normal, t[0]) - plane.offset, dot(plane.normal, t[1]) - plane.offset, dot(plane.normal, t[2]) - plane.offset };
                for (int e = 0; e < 3; ++e) {
                    const double a = d[e], b = d[(e + 1) % 3];
                    if ((a < 0 && b > 0) || (a > 0 && b < 0)) {
                        const Point x = t[e] + (t[(e + 1) % 3] - t[e]) * (a / (a - b));
                        below.push_back(x);
                        above.push_back(x);
                    }
                }
            }
        }

        // A cut must not leave a sliver: hull builders (qhull here, Havok in
        // game) treat one thinner than this unreliably, and it adds no grip.
        constexpr double kMinCutWidth = .2;

        // The hull's thinnest extent: the smallest depth behind any face.
        double width(const Hull& hull)
        {
            double thinnest = kInfinity;
            for (const auto& f : hull.faces) {
                const Point a = hull.points[f[0]];
                const Point n = unit(cross(hull.points[f[1]] - a, hull.points[f[2]] - a));
                double depth = 0;
                for (const auto& p : hull.points) depth = (std::max)(depth, -dot(n, p - a));
                thinnest = (std::min)(thinnest, depth);
            }
            return thinnest;
        }

        // The best cut of one piece, or false when none reduces its bulge.
        bool split(const Piece& piece, const Settings& s, Piece& below, Piece& above)
        {
            const NearestTree tree(piece.mesh);
            // A cut must remove a real share of the bulge: a bore or hollow no
            // plane can open would otherwise spend the hull budget for nothing.
            const double required = piece.bulge * .9;
            double bestScore = required;
            Plane bestPlane{};
            std::size_t bestAxis = 0;
            bool found = false;
            std::vector<Point> lo, hi;
            auto cuts = piece.cuts;
            cuts.emplace_back();
            // Candidates are ranked on a coarser hull; the winner is refitted.
            Settings scoring = s;
            scoring.targetHullPoints = kScoreHullPoints;
            scoring.fineTolerance = (std::max)(s.fineTolerance, kScoreFineTolerance);
            const auto evaluate = [&](Plane plane, std::size_t axis) {
                sides(piece, plane, lo, hi);
                if (lo.size() < 4 || hi.size() < 4) return;
                // Score against the parent's surface: children's own trees are
                // built only for the accepted cut.
                cuts.back() = plane;
                double score = 0;
                for (const auto* side : { &lo, &hi }) {
                    const auto hull = fitHull(*side, scoring);
                    if (hull.faces.empty() || width(hull) < kMinCutWidth) return;
                    score += concavity(hull, tree, cuts, kScoreSamples).integral;
                    if (score >= bestScore) return;
                }
                bestScore = score;
                bestPlane = plane;
                bestAxis = axis;
                found = true;
            };
            // Coarse: evenly spaced offsets on every axis, plus through the
            // bulge; then refine around the best offset found.
            const auto directions = axes(piece);
            for (std::size_t a = 0; a < directions.size(); ++a) {
                const auto& axis = directions[a];
                for (int k = 1; k <= kOffsetsPerNormal; ++k) {
                    evaluate({ axis.normal, axis.lo + (axis.hi - axis.lo) * k / (kOffsetsPerNormal + 1.0) }, a);
                }
                if (axis.through > axis.lo && axis.through < axis.hi) evaluate({ axis.normal, axis.through }, a);
            }
            if (found) {
                const auto& axis = directions[bestAxis];
                const double step = (axis.hi - axis.lo) / (kOffsetsPerNormal + 1.0);
                const double center = bestPlane.offset;
                for (const double f : { -.5, -.25, .25, .5 }) {
                    const double offset = center + f * step;
                    if (offset > axis.lo && offset < axis.hi) evaluate({ axis.normal, offset }, bestAxis);
                }
            }
            if (!found) return false;
            below = {};
            above = {};
            clipMesh(piece.mesh, bestPlane, below.mesh, above.mesh);
            below.cuts = above.cuts = piece.cuts;
            below.cuts.push_back(bestPlane);
            above.cuts.push_back(bestPlane);
            // The score is a cheap estimate; keep the cut only if the full
            // measurement of both children confirms it.
            return measure(below, s, kMeasureSamples) && measure(above, s, kMeasureSamples)
                && below.bulge + above.bulge <= required;
        }
    }

    namespace
    {
        struct Box
        {
            Point lo{ kInfinity, kInfinity, kInfinity }, hi{ -kInfinity, -kInfinity, -kInfinity };
            void add(Point p)
            {
                lo = { (std::min)(lo.x, p.x), (std::min)(lo.y, p.y), (std::min)(lo.z, p.z) };
                hi = { (std::max)(hi.x, p.x), (std::max)(hi.y, p.y), (std::max)(hi.z, p.z) };
            }
            double distance(const Box& o) const
            {
                const double dx = (std::max)({ lo.x - o.hi.x, 0.0, o.lo.x - hi.x });
                const double dy = (std::max)({ lo.y - o.hi.y, 0.0, o.lo.y - hi.y });
                const double dz = (std::max)({ lo.z - o.hi.z, 0.0, o.lo.z - hi.z });
                return std::sqrt(dx * dx + dy * dy + dz * dz);
            }
        };

        struct Unions
        {
            std::vector<std::size_t> parent;
            explicit Unions(std::size_t n) : parent(n) { std::iota(parent.begin(), parent.end(), 0); }
            std::size_t root(std::size_t i)
            {
                while (parent[i] != i) { parent[i] = parent[parent[i]]; i = parent[i]; }
                return i;
            }
            bool join(std::size_t a, std::size_t b)
            {
                a = root(a); b = root(b);
                if (a == b) return false;
                parent[b] = a;
                return true;
            }
        };

        // Welded islands with outward winding, as ROCK's island pass makes
        // them, but without its join-everything step above eight islands.
        std::vector<gap::Mesh> weldIslands(std::span<const Triangle> triangles, double weld)
        {
            Unions sets(triangles.size());
            std::unordered_map<std::array<std::int64_t, 3>, std::size_t, gap::detail::KeyHash> owners;
            owners.reserve(triangles.size() * 2);
            for (std::size_t i = 0; i < triangles.size(); ++i) {
                for (const auto& p : triangles[i]) {
                    auto [it, inserted] = owners.emplace(std::array<std::int64_t, 3>{ std::llround(p.x / weld), std::llround(p.y / weld), std::llround(p.z / weld) }, i);
                    if (!inserted) sets.join(it->second, i);
                }
            }
            std::vector<gap::Mesh> islands;
            std::unordered_map<std::size_t, std::size_t> slot;
            for (std::size_t i = 0; i < triangles.size(); ++i) {
                auto [it, inserted] = slot.emplace(sets.root(i), islands.size());
                if (inserted) islands.emplace_back();
                islands[it->second].push_back(triangles[i]);
            }
            for (auto& island : islands) {
                const Point origin = island.front()[0];
                double volume = 0;
                for (const auto& t : island) volume += dot(t[0] - origin, cross(t[1] - origin, t[2] - origin));
                if (volume < 0) for (auto& t : island) std::swap(t[1], t[2]);
            }
            return islands;
        }

        // Single-linkage clusters: islands nearer than the preserved gap
        // always share a piece; further ones merge, nearest first, only while
        // the source has more than maxPieces; a flat island joins its nearest
        // neighbour. Islands beyond the pair budget fall back to ROCK's join.
        std::vector<gap::Mesh> clusterIslands(std::vector<gap::Mesh> islands, double gapSize, std::size_t maxPieces)
        {
            const std::size_t n = islands.size();
            if (n <= 1) return islands;
            if (n > 2048) {
                gap::Mesh joined;
                for (auto& island : islands) joined.insert(joined.end(), island.begin(), island.end());
                return { std::move(joined) };
            }
            std::vector<Box> boxes(n);
            std::vector<char> flat(n);
            for (std::size_t i = 0; i < n; ++i) {
                for (const auto& t : islands[i]) for (const auto& p : t) boxes[i].add(p);
                bool valid = false;
                finish(gap::detail::hasVolume(islands[i], valid));
                flat[i] = !valid;
            }
            Unions sets(n);
            std::size_t clusters = n;
            // A flat island has no hull of its own: it joins the nearest
            // island with volume (or the first island if none has volume).
            for (std::size_t i = 0; i < n; ++i) {
                if (!flat[i]) continue;
                std::size_t nearest = i == 0 ? 1 : 0;
                double best = kInfinity;
                for (std::size_t j = 0; j < n; ++j) {
                    if (j != i && !flat[j] && boxes[i].distance(boxes[j]) < best) { best = boxes[i].distance(boxes[j]); nearest = j; }
                }
                if (sets.join(nearest, i)) --clusters;
            }
            struct Pair { double distance; std::uint32_t a, b; };
            std::vector<Pair> pairs;
            pairs.reserve(n * (n - 1) / 2);
            for (std::size_t a = 0; a < n; ++a) {
                for (std::size_t b = a + 1; b < n; ++b) {
                    if (!flat[a] && !flat[b]) pairs.push_back({ boxes[a].distance(boxes[b]), std::uint32_t(a), std::uint32_t(b) });
                }
            }
            std::sort(pairs.begin(), pairs.end(), [](const Pair& x, const Pair& y) { return x.distance < y.distance; });
            for (const auto& pair : pairs) {
                if (clusters <= maxPieces && pair.distance > gapSize) break;
                if (sets.join(pair.a, pair.b)) --clusters;
            }
            std::vector<gap::Mesh> out;
            std::unordered_map<std::size_t, std::size_t> slot;
            for (std::size_t i = 0; i < n; ++i) {
                auto [it, inserted] = slot.emplace(sets.root(i), out.size());
                if (inserted) out.emplace_back();
                auto& target = out[it->second];
                target.insert(target.end(), islands[i].begin(), islands[i].end());
            }
            return out;
        }
    }

    Hull fitHull(std::span<const Point> points, const Settings& settings)
    {
        return QuickHull(points, settings).run();
    }

    bool negligible(std::span<const Triangle> triangles, double minExtent)
    {
        Box box;
        for (const auto& t : triangles) {
            for (const auto& p : t) box.add(p);
        }
        const Point span = box.hi - box.lo;
        return !triangles.empty() && span.x < minExtent && span.y < minExtent && span.z < minExtent;
    }

    Result decompose(std::span<const Triangle> triangles, const Settings& s, const std::atomic<bool>* cancel)
    {
        Result result;
        const auto cancelled = [&] { return cancel && cancel->load(); };
        if (triangles.empty() || triangles.size() > gap::kMaxInputTriangles) {
            result.error = "Source triangle count is outside ROCK's limits";
            return result;
        }

        // Stage 1: welded islands, clustered to the hull budget.
        auto parts = clusterIslands(weldIslands(triangles, s.weld), s.gap, s.maxPieces);
        std::vector<Piece> pieces;
        for (std::size_t i = 0; i < parts.size(); ++i) {
            Piece piece;
            piece.mesh = std::move(parts[i]);
            if (!measure(piece, s, kMeasureSamples)) {
                // As in ROCK: a piece without volume sends the whole source
                // to the complete-source fallback.
                result.error = "an island cluster has no volume";
                return result;
            }
            pieces.push_back(std::move(piece));
            if (cancelled()) { result.error = "cancelled"; return result; }
        }


        // Stage 2: cut the worst bulge while the source has hull budget.
        while (pieces.size() < s.maxPieces) {
            if (cancelled()) { result.error = "cancelled"; return result; }
            std::size_t worst = pieces.size();
            double worstBulge = 0;
            for (std::size_t i = 0; i < pieces.size(); ++i) {
                if (!pieces[i].final && pieces[i].concavity > s.concavity && pieces[i].bulge > worstBulge) {
                    worstBulge = pieces[i].bulge;
                    worst = i;
                }
            }
            if (worst == pieces.size()) break;
            Piece below, above;
            if (!split(pieces[worst], s, below, above)) {
                pieces[worst].final = true;
                continue;
            }
            pieces[worst] = std::move(below);
            pieces.push_back(std::move(above));
            ++result.concavityCuts;
        }

        // Fail closed: each hull must be a convex envelope of its points,
        // every vertex of its piece must lie inside it within the tolerance,
        // and every source triangle (vertices, edge midpoints, centre) must
        // lie inside some hull; otherwise the source takes ROCK's fallback.
        const double limit = s.hullTolerance + 1e-3;
        std::vector<std::vector<Plane>> planes(pieces.size());
        const auto inside = [&](std::size_t k, Point p) {
            for (const auto& plane : planes[k]) {
                if (dot(plane.normal, p) - plane.offset > limit) return false;
            }
            return true;
        };
        for (std::size_t k = 0; k < pieces.size(); ++k) {
            const auto& hull = pieces[k].hull;
            for (const auto& f : hull.faces) {
                const Point n = unit(cross(hull.points[f[1]] - hull.points[f[0]], hull.points[f[2]] - hull.points[f[0]]));
                const Plane plane{ n, dot(n, hull.points[f[0]]) };
                for (const auto& q : hull.points) {
                    if (dot(n, q) - plane.offset > 1e-3) {
                        char line[160];
                        std::snprintf(line, sizeof line, "piece %zu hull is not convex (%zu points)", k, hull.points.size());
                        result.error = line;
                        return result;
                    }
                }
                planes[k].push_back(plane);
            }
            for (const auto& t : pieces[k].mesh) {
                for (const auto& q : t) {
                    if (!inside(k, q)) {
                        char line[160];
                        std::snprintf(line, sizeof line, "piece %zu of %zu leaves surface outside its hull (%zu triangles, %zu hull points)",
                            k, pieces.size(), pieces[k].mesh.size(), hull.points.size());
                        result.error = line;
                        return result;
                    }
                }
            }
        }
        {
            std::size_t last = 0; // neighbouring triangles usually share a hull
            for (const auto& t : triangles) {
                const std::array<Point, 7> samples{ t[0], t[1], t[2], gap::center(t), (t[0] + t[1]) * .5, (t[1] + t[2]) * .5, (t[2] + t[0]) * .5 };
                for (const auto& q : samples) {
                    bool covered = inside(last, q);
                    for (std::size_t k = 0; k < pieces.size() && !covered; ++k) {
                        if (k != last && inside(k, q)) { covered = true; last = k; }
                    }
                    if (!covered) {
                        char line[200];
                        std::snprintf(line, sizeof line, "source point (%.2f,%.2f,%.2f) lies outside every piece", q.x, q.y, q.z);
                        result.error = line;
                        return result;
                    }
                }
            }
        }
        for (auto& piece : pieces) {
            result.worstConcavity = (std::max)(result.worstConcavity, piece.concavity);
            result.pieces.push_back(std::move(piece.hull));
        }
        result.budgetLimited = pieces.size() >= s.maxPieces && result.worstConcavity > s.concavity;
        return result;
    }
}
