#include "areas/collisions/CollisionQuality.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace reloadmaker::collision_quality
{
    namespace
    {
        using Vec = std::array<double, 3>;
        Vec sub(const Vec& a, const Vec& b) { return { a[0] - b[0], a[1] - b[1], a[2] - b[2] }; }
        Vec add(const Vec& a, const Vec& b) { return { a[0] + b[0], a[1] + b[1], a[2] + b[2] }; }
        Vec scale(const Vec& a, double s) { return { a[0] * s, a[1] * s, a[2] * s }; }
        double dot(const Vec& a, const Vec& b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
        Vec cross(const Vec& a, const Vec& b) { return { a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0] }; }
        double length(const Vec& a) { return std::sqrt(dot(a, a)); }

        constexpr int kMeshSamples = 2000;
        constexpr int kHullSamples = 800;

        // Deterministic low-discrepancy sequence, so runs compare exactly.
        double halton(int index, int base)
        {
            double f = 1, r = 0;
            for (int i = index + 1; i > 0; i /= base) {
                f /= base;
                r += f * (i % base);
            }
            return r;
        }

        struct Triangle
        {
            Vec a, b, c;
        };

        // Area-weighted points on a triangle set.
        std::vector<Vec> sample(const std::vector<Triangle>& triangles, int count, std::vector<int>* owners = nullptr)
        {
            std::vector<double> cumulative;
            cumulative.reserve(triangles.size());
            double total = 0;
            for (const auto& t : triangles) {
                total += length(cross(sub(t.b, t.a), sub(t.c, t.a))) * .5;
                cumulative.push_back(total);
            }
            std::vector<Vec> points;
            if (total <= 0) {
                return points;
            }
            points.reserve(count);
            for (int i = 0; i < count; ++i) {
                const double target = (i + halton(i, 2)) / count * total;
                const auto index = std::min<std::size_t>(std::lower_bound(cumulative.begin(), cumulative.end(), target) - cumulative.begin(), triangles.size() - 1);
                double u = halton(i, 3), v = halton(i, 5);
                if (u + v > 1) {
                    u = 1 - u;
                    v = 1 - v;
                }
                const auto& t = triangles[index];
                points.push_back(add(t.a, add(scale(sub(t.b, t.a), u), scale(sub(t.c, t.a), v))));
                if (owners) {
                    owners->push_back(int(index));
                }
            }
            return points;
        }

        // Closest point on a triangle (Ericson, Real-Time Collision Detection 5.1.5).
        Vec closestOnTriangle(const Vec& p, const Triangle& t)
        {
            const auto ab = sub(t.b, t.a), ac = sub(t.c, t.a), ap = sub(p, t.a);
            const double d1 = dot(ab, ap), d2 = dot(ac, ap);
            if (d1 <= 0 && d2 <= 0) return t.a;
            const auto bp = sub(p, t.b);
            const double d3 = dot(ab, bp), d4 = dot(ac, bp);
            if (d3 >= 0 && d4 <= d3) return t.b;
            const double vc = d1 * d4 - d3 * d2;
            if (vc <= 0 && d1 >= 0 && d3 <= 0) return add(t.a, scale(ab, d1 / (d1 - d3)));
            const auto cp = sub(p, t.c);
            const double d5 = dot(ab, cp), d6 = dot(ac, cp);
            if (d6 >= 0 && d5 <= d6) return t.c;
            const double vb = d5 * d2 - d1 * d6;
            if (vb <= 0 && d2 >= 0 && d6 <= 0) return add(t.a, scale(ac, d2 / (d2 - d6)));
            const double va = d3 * d6 - d5 * d4;
            if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) {
                return add(t.b, scale(sub(t.c, t.b), (d4 - d3) / ((d4 - d3) + (d5 - d6))));
            }
            const double denominator = 1 / (va + vb + vc);
            return add(t.a, add(scale(ab, vb * denominator), scale(ac, vc * denominator)));
        }

        struct Plane
        {
            Vec normal;
            double offset;
        };

        // Outward face planes; the signed distance to the hull is the largest
        // plane distance (exact inside, a lower bound outside).
        // Each face points away from the hull's points. Judging by the
        // centroid alone flips faces of thin sliver hulls, whose centroid
        // lies within rounding of their large faces.
        std::vector<Plane> planes(const Hull& hull)
        {
            std::vector<Plane> out;
            for (const auto& f : hull.faces) {
                const auto& a = hull.points[f[0]];
                auto n = cross(sub(hull.points[f[1]], a), sub(hull.points[f[2]], a));
                const double l = length(n);
                if (l < 1e-12) {
                    continue;
                }
                n = scale(n, 1 / l);
                double d = dot(n, a);
                double front = 0, back = 0;
                for (const auto& p : hull.points) {
                    const double s = dot(n, p) - d;
                    front = std::max(front, s);
                    back = std::min(back, s);
                }
                if (front > -back) {
                    n = scale(n, -1);
                    d = -d;
                }
                out.push_back({ n, d });
            }
            return out;
        }

        double signedDistance(const std::vector<Plane>& hull, const Vec& p)
        {
            double d = -std::numeric_limits<double>::infinity();
            for (const auto& plane : hull) {
                d = std::max(d, dot(plane.normal, p) - plane.offset);
            }
            return d;
        }

        double percentile(std::vector<double> values, double fraction)
        {
            if (values.empty()) {
                return 0;
            }
            const auto index = std::min<std::size_t>(std::size_t(fraction * (values.size() - 1) + .5), values.size() - 1);
            std::nth_element(values.begin(), values.begin() + index, values.end());
            return values[index];
        }
    }

    Quality measure(const Mesh& mesh, const std::vector<Hull>& hulls)
    {
        Quality quality;
        std::vector<Triangle> triangles;
        triangles.reserve(mesh.triangles.size());
        for (const auto& t : mesh.triangles) {
            triangles.push_back({ mesh.vertices[t[0]], mesh.vertices[t[1]], mesh.vertices[t[2]] });
        }
        std::vector<std::vector<Plane>> hullPlanes;
        for (const auto& hull : hulls) {
            hullPlanes.push_back(planes(hull));
        }
        if (triangles.empty() || hullPlanes.empty()) {
            return quality;
        }

        const auto meshPoints = sample(triangles, kMeshSamples);
        std::vector<double> uncovered;
        uncovered.reserve(meshPoints.size());
        int outside = 0;
        for (const auto& p : meshPoints) {
            double d = std::numeric_limits<double>::infinity();
            for (const auto& hull : hullPlanes) {
                d = std::min(d, signedDistance(hull, p));
            }
            d = std::max(0.0, d);
            outside += d > kUncoveredTolerance;
            uncovered.push_back(d);
        }
        quality.meshSamples = int(meshPoints.size());
        quality.uncoveredFraction = meshPoints.empty() ? 0 : double(outside) / meshPoints.size();
        quality.uncoveredP95 = percentile(uncovered, .95);
        quality.uncoveredMax = uncovered.empty() ? 0 : *std::max_element(uncovered.begin(), uncovered.end());

        // Bounding spheres skip most exact triangle tests.
        std::vector<std::pair<Vec, double>> spheres;
        spheres.reserve(triangles.size());
        for (const auto& t : triangles) {
            const auto c = scale(add(t.a, add(t.b, t.c)), 1.0 / 3);
            spheres.push_back({ c, std::max({ length(sub(t.a, c)), length(sub(t.b, c)), length(sub(t.c, c)) }) });
        }
        std::vector<Triangle> hullTriangles;
        std::vector<int> hullOf;
        for (std::size_t h = 0; h < hulls.size(); ++h) {
            for (const auto& f : hulls[h].faces) {
                hullTriangles.push_back({ hulls[h].points[f[0]], hulls[h].points[f[1]], hulls[h].points[f[2]] });
                hullOf.push_back(int(h));
            }
        }
        std::vector<int> owners;
        const auto hullPoints = sample(hullTriangles, kHullSamples, &owners);
        std::vector<double> overfill;
        for (std::size_t i = 0; i < hullPoints.size(); ++i) {
            const auto& p = hullPoints[i];
            const int own = hullOf[owners[i]];
            bool interior = false;
            for (std::size_t h = 0; h < hullPlanes.size() && !interior; ++h) {
                interior = int(h) != own && signedDistance(hullPlanes[h], p) <= .01;
            }
            if (interior) {
                continue;
            }
            double best = std::numeric_limits<double>::infinity();
            Vec closest{};
            std::size_t owner = 0;
            for (std::size_t t = 0; t < triangles.size(); ++t) {
                if (length(sub(p, spheres[t].first)) - spheres[t].second >= best) {
                    continue;
                }
                const auto q = closestOnTriangle(p, triangles[t]);
                const double d = length(sub(p, q));
                if (d < best) {
                    best = d;
                    closest = q;
                    owner = t;
                }
            }
            // Hull surface behind the nearest triangle is inside the solid.
            const auto& t = triangles[owner];
            if (dot(sub(p, closest), cross(sub(t.b, t.a), sub(t.c, t.a))) < 0) {
                best = 0;
            }
            overfill.push_back(best);
        }
        quality.hullSamples = int(overfill.size());
        quality.overfillP95 = percentile(overfill, .95);
        quality.overfillMax = overfill.empty() ? 0 : *std::max_element(overfill.begin(), overfill.end());
        return quality;
    }
}
