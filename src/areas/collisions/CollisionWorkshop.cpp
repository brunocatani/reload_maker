// The collision workshop document and its operations: generating convex
// hulls for a captured target on a cancellable worker, opening, saving and
// exporting profiles, and editing hulls, world pieces and physics.
#include "areas/collisions/CollisionWorkshop.h"
#include "StudioPaths.h"
#include "commands/CommandSupport.h"

#include <QCryptographicHash>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QRegularExpression>
#include <QSaveFile>
#include <QtConcurrent>
#include <algorithm>
#include <cmath>

namespace reloadmaker
{
    using commands::require;

    namespace
    {
        // Rounded to the field's decimals, then bounded, as QDoubleSpinBox
        // stores a value.
        double formValue(const CollisionFormField& field, double value)
        {
            return std::clamp(QString::number(value, 'f', field.decimals).toDouble(), field.minimum, field.maximum);
        }
    }

    CollisionWorkshop::CollisionWorkshop(QObject* parent)
        : QObject(parent)
    {
        connect(&_worker, &QFutureWatcher<CollisionResult>::finished, this, [this] { finishGeneration(); });
        _frame.setTimerType(Qt::PreciseTimer);
        connect(&_frame, &QTimer::timeout, this, [this] { pollWorkerTest(); });
    }

    CollisionWorkshop::~CollisionWorkshop()
    {
        cancel();
        _worker.waitForFinished();
        _frame.stop();
        _buildWorker.reset();
    }

    // A cancelled generation leaves the profile as it was; a finished one
    // keeps the replaced profile as the comparison baseline.
    void CollisionWorkshop::finishGeneration()
    {
        const auto result = _worker.result();
        _generating = false;
        if (_cancel && _cancel->load()) {
            update();
            return;
        }
        if (!result.error.isEmpty()) {
            reportError(result.error);
            return;
        }
        _baseline = _profile;
        _profile = result.profile;
        _dirty = true;
        _lastError.clear();
        update(true);
    }

    void CollisionWorkshop::cancel()
    {
        if (_cancel) {
            _cancel->store(true);
        }
        if (_frame.isActive()) {
            _buildWorker->advanceGeneration();
            finishWorkerTest("Cancelled");
        }
    }

    void CollisionWorkshop::startWorkerTest(const QJsonObject& args)
    {
        applyGenerationSettings("worker-test", args);
        _workerTest = {};
        _workerTest.input = context(false);
        if (args.contains("framePeriodMs")) {
            const auto period = args["framePeriodMs"].toDouble(-1);
            require(period >= 1 && period <= 100, "framePeriodMs must be 1-100");
            _workerTest.framePeriodMs = period;
        }
        if (args.contains("supersedeAfterMs")) {
            const auto after = args["supersedeAfterMs"].toDouble(-1);
            require(after >= 0 && after <= 60000, "supersedeAfterMs must be 0-60000");
            _workerTest.supersedeAfterMs = after;
        }
        if (!_buildWorker) {
            _buildWorker = std::make_unique<collision_worker::Worker>();
        }
        _buildWorker->advanceGeneration();
        _generating = true;
        _workerTest.clock.start();
        submitWorkerJobs();
        _frame.start(std::max(1, int(std::lround(_workerTest.framePeriodMs))));
    }

    // One job per included source: the same bake, one source at a time.
    void CollisionWorkshop::submitWorkerJobs()
    {
        _workerTest.results.clear();
        _workerTest.submitted = 0;
        for (const auto& source : _workerTest.input.sources) {
            if (!source.worldIncluded) {
                continue;
            }
            auto one = _workerTest.input;
            one.sources = { source };
            _buildWorker->submit([one](const std::atomic<bool>& cancel) mutable -> std::shared_ptr<void> {
                // bakeCollision only reads its stop flag; alias the worker's.
                const std::shared_ptr<std::atomic<bool>> stop(std::shared_ptr<void>{}, const_cast<std::atomic<bool>*>(&cancel));
                return std::make_shared<CollisionResult>(bakeCollision(std::move(one), stop));
            });
            ++_workerTest.submitted;
        }
        if (_workerTest.submitted == 0) {
            finishWorkerTest("No included source pieces");
        }
    }

    // The per-frame pickup ROCK would do: take finished jobs, never wait.
    void CollisionWorkshop::pollWorkerTest()
    {
        auto& test = _workerTest;
        if (test.supersedeAfterMs >= 0 && !test.superseded && test.clock.elapsed() >= test.supersedeAfterMs) {
            // An attachment change mid-build: the old generation's work is
            // cancelled or discarded and the same sources are queued again.
            test.superseded = true;
            _buildWorker->advanceGeneration();
            submitWorkerJobs();
        }
        QElapsedTimer poll;
        poll.start();
        const auto outcomes = _buildWorker->poll();
        for (const auto& outcome : outcomes) {
            const auto run = std::chrono::duration<double, std::milli>(outcome.finished - outcome.started).count();
            const auto queue = std::chrono::duration<double, std::milli>(outcome.started - outcome.submitted).count();
            const auto latency = std::chrono::duration<double, std::milli>(collision_worker::Clock::now() - outcome.submitted).count();
            test.runMsTotal += run;
            test.runMsMax = std::max(test.runMsMax, run);
            test.queueMsMax = std::max(test.queueMsMax, queue);
            test.latencyMsMax = std::max(test.latencyMsMax, latency);
            if (!outcome.error.empty()) {
                test.errors << QString::fromStdString(outcome.error);
            }
            test.results.insert(outcome.job, outcome.payload ? *std::static_pointer_cast<CollisionResult>(outcome.payload) : CollisionResult{});
        }
        const double pollMs = poll.nsecsElapsed() / 1e6;
        ++test.polls;
        test.pollMsTotal += pollMs;
        test.pollMsMax = std::max(test.pollMsMax, pollMs);
        if (std::uint64_t(test.results.size()) >= test.submitted) {
            finishWorkerTest();
        }
    }

    void CollisionWorkshop::finishWorkerTest(const QString& error)
    {
        _frame.stop();
        _generating = false;
        auto& test = _workerTest;
        auto merged = test.input;
        merged.hulls.clear();
        merged.metrics.clear();
        merged.warnings.clear();
        merged.inputTriangles = 0;
        for (const auto& result : test.results) {
            if (!result.error.isEmpty()) {
                merged.warnings << result.error;
                continue;
            }
            merged.hulls += result.profile.hulls;
            merged.metrics += result.profile.metrics;
            merged.warnings += result.profile.warnings;
            merged.inputTriangles += result.profile.inputTriangles;
            merged.origin = result.profile.origin;
            merged.sourceHash = result.profile.sourceHash;
        }
        _workerReport = QJsonObject{ { "algorithm", QString::fromLatin1(collisionAlgorithmName(test.input.settings.algorithm)) },
                                     { "jobs", double(test.submitted) },
                                     { "completed", int(test.results.size()) },
                                     { "wallMs", double(test.clock.elapsed()) },
                                     { "framePeriodMs", test.framePeriodMs },
                                     { "polls", test.polls },
                                     { "pollMsMean", test.polls ? test.pollMsTotal / test.polls : 0.0 },
                                     { "pollMsMax", test.pollMsMax },
                                     { "jobRunMsTotal", test.runMsTotal },
                                     { "jobRunMsMax", test.runMsMax },
                                     { "jobQueueMsMax", test.queueMsMax },
                                     { "jobLatencyMsMax", test.latencyMsMax },
                                     { "superseded", test.superseded },
                                     { "errors", QJsonArray::fromStringList(test.errors) },
                                     { "error", error } };
        if (error.isEmpty() && merged.valid()) {
            _baseline = _profile;
            _profile = std::move(merged);
            _dirty = true;
            _lastError.clear();
            update(true);
            return;
        }
        _lastError = error.isEmpty() ? QString("Worker test produced no hulls") : error;
        update();
    }

    // The scene changed: the profile no longer matches it.
    void CollisionWorkshop::invalidate()
    {
        cancel();
        _profile = {};
        _baseline = {};
        _dirty = false;
        _lastPath.clear();
        _lastError.clear();
        update();
    }

    // New targets invalidate the profile and select the first one.
    void CollisionWorkshop::setTargets(const QStringList& names)
    {
        if (names == _targets) {
            return;
        }
        _targets = names;
        _targetIndex = names.isEmpty() ? -1 : 0;
        invalidate();
    }

    // Choosing a target, even the current one, discards the profile.
    void CollisionWorkshop::chooseTarget(int index)
    {
        _targetIndex = index;
        invalidate();
    }

    void CollisionWorkshop::reportError(const QString& error)
    {
        _lastError = error;
        update();
    }

    void CollisionWorkshop::selectHull(int index)
    {
        if (index == _selectedHull) {
            return;
        }
        _selectedHull = index;
        emit changed(false);
    }

    void CollisionWorkshop::setHullEnabled(int index, bool enabled)
    {
        if (index < 0 || index >= _profile.hulls.size()) {
            return;
        }
        _profile.hulls[index].enabled = enabled;
        _dirty = true;
        update(true);
    }

    // An excluded world piece also disables the hulls generated from it.
    void CollisionWorkshop::setPieceIncluded(int index, bool included)
    {
        if (index < 0 || index >= _profile.sources.size()) {
            return;
        }
        auto& source = _profile.sources[index];
        source.worldIncluded = included;
        if (!source.worldIncluded) {
            for (auto& hull : _profile.hulls) {
                if (hull.source == source.key) {
                    hull.enabled = false;
                }
            }
        }
        _dirty = true;
        update(true);
    }

    void CollisionWorkshop::setGap(double value)
    {
        _form.gap = formValue(kCollisionGap, value);
    }

    void CollisionWorkshop::setSupportError(double value)
    {
        _form.supportError = formValue(kCollisionSupportError, value);
    }

    void CollisionWorkshop::setTargetPoints(double value)
    {
        _form.targetPoints = formValue(kCollisionTargetPoints, value);
    }

    void CollisionWorkshop::setMass(double value)
    {
        value = formValue(kCollisionMass, value);
        if (value == _form.mass) {
            return;
        }
        _form.mass = value;
        applyPhysicalForm();
    }

    void CollisionWorkshop::setRadius(double value)
    {
        value = formValue(kCollisionRadius, value);
        if (value == _form.radius) {
            return;
        }
        _form.radius = value;
        applyPhysicalForm();
    }

    void CollisionWorkshop::setAlgorithm(CollisionAlgorithm algorithm)
    {
        _form.algorithm = algorithm;
    }

    void CollisionWorkshop::setConcavity(double value)
    {
        _form.concavity = formValue(kCollisionConcavity, value);
    }

    // Mass and convex radius edit the current profile directly; without one
    // they wait for the next generation.
    void CollisionWorkshop::applyPhysicalForm()
    {
        if (!_profile.valid()) {
            return;
        }
        _profile.settings.mass = _form.mass;
        _profile.settings.radius = _form.radius;
        _dirty = true;
        update(true);
    }

    void CollisionWorkshop::setOverlay(bool shown)
    {
        if (shown == _overlay) {
            return;
        }
        _overlay = shown;
        emit changed(false);
    }

    void CollisionWorkshop::setCompare(bool shown)
    {
        if (shown == _compare) {
            return;
        }
        _compare = shown;
        emit changed(false);
    }

    void CollisionWorkshop::setIsolated(bool isolated)
    {
        if (isolated == _isolated) {
            return;
        }
        _isolated = isolated;
        emit changed(false);
    }

    // The hull selection stays within the hulls, and the form shows the
    // profile's mass and radius.
    void CollisionWorkshop::update(bool authored)
    {
        const int count = int(_profile.hulls.size());
        _selectedHull = count ? std::clamp(_selectedHull, 0, count - 1) : -1;
        if (_profile.valid()) {
            _form.mass = formValue(kCollisionMass, _profile.settings.mass);
            _form.radius = formValue(kCollisionRadius, _profile.settings.radius);
        }
        emit updated();
        emit changed(authored);
    }

    // A fresh snapshot of the selected target with the form's settings. The
    // same pieces of the same profile keep their world inclusion.
    CollisionProfile CollisionWorkshop::context(bool referenceOnly)
    {
        require(bool(snapshot), "No scene provider");
        auto p = snapshot(_targetIndex, referenceOnly);
        if (p.weapon == _profile.weapon && p.configuration == _profile.configuration && p.target == _profile.target) {
            for (auto& source : p.sources) {
                for (const auto& old : _profile.sources) {
                    if (source.key == old.key) {
                        source.worldIncluded = old.worldIncluded;
                    }
                }
            }
        }
        p.settings.gap = _form.gap;
        p.settings.error = _form.supportError;
        p.settings.targetPoints = int(_form.targetPoints);
        p.settings.mass = _form.mass;
        p.settings.radius = _form.radius;
        p.settings.algorithm = _form.algorithm;
        p.settings.concavity = _form.concavity;
        return p;
    }

    // One file per weapon, loadout and target under collisions/.
    QString CollisionWorkshop::savePath() const
    {
        const QString target = _targetIndex >= 0 && _targetIndex < _targets.size() ? _targets[_targetIndex] : QString();
        QString name = _profile.weapon + "_" + target;
        name.replace(QRegularExpression("[^A-Za-z0-9_-]+"), "_");
        name = name.left(80);
        const auto key = (_profile.weapon + "|" + _profile.configuration + "|" + _profile.target).toUtf8();
        return studioDataPath("collisions/" + name + "_" + QString::fromLatin1(QCryptographicHash::hash(key, QCryptographicHash::Sha256).toHex()) + ".json");
    }

    QJsonObject CollisionWorkshop::state() const
    {
        auto out = collisionSummary(_profile);
        QJsonArray targets;
        for (int i = 0; i < _targets.size(); ++i) {
            targets.append(QJsonObject{ { "index", i }, { "name", _targets[i] } });
        }
        out["targets"] = targets;
        out["targetIndex"] = _targetIndex;
        out["busy"] = _generating;
        out["dirty"] = _dirty;
        out["error"] = _lastError;
        out["path"] = _lastPath;
        out["selectedHull"] = _selectedHull;
        out["overlay"] = _overlay;
        out["compare"] = _compare;
        out["isolated"] = _isolated;
        out["workerTest"] = _workerReport;
        out["form"] = QJsonObject{ { "algorithm", QString::fromLatin1(collisionAlgorithmName(_form.algorithm)) },
                                   { "concavityGame", _form.concavity } };
        return out;
    }

    // cancel; and with an optional target: select, prepare, cook, defaults,
    // generate, open, browse_profile, save, export, browse_export,
    // export-hulls, piece, hull, view.
    QJsonObject CollisionWorkshop::command(const QJsonObject& args)
    {
        const auto op = args["operation"].toString();
        if (op == "cancel") {
            cancel();
            return state();
        }
        require(!_generating, "Collision generation is still running; wait or cancel");
        if (args.contains("target")) {
            const auto index = args["target"].toInt(-1);
            require(index >= 0 && index < _targets.size(), "Unknown collision target");
            if (index != _targetIndex) {
                _targetIndex = index;
                invalidate();
            }
        }
        _lastError.clear();
        bool authored = false;
        if (op == "select") {
        } else if (op == "prepare") {
            require(bool(prepare), "Visible part capture is unavailable");
            prepare();
            return state();
        } else if (op == "cook") {
            require(_profile.valid() && bool(cook), "Generate a magazine collision profile first");
            cook();
            return state();
        } else if (op == "defaults" || op == "generate") {
            generate(op, args);
        } else if (op == "worker-test") {
            startWorkerTest(args);
        } else if (op == "open" || op == "browse_profile") {
            QString path = args["path"].toString();
            if (op == "browse_profile") {
                path = chooseProfile ? chooseProfile() : QString();
            }
            if (path.isEmpty()) {
                return state();
            }
            openProfile(path);
            authored = true;
        } else if (op == "save" || op == "export" || op == "browse_export" || op == "export-hulls") {
            require(_profile.valid(), "Generate or load a profile first");
            require(std::any_of(_profile.hulls.begin(), _profile.hulls.end(), [](const auto& h) { return h.enabled; }), "Enable at least one hull");
            QString path = op == "save" ? savePath() : args["path"].toString();
            if (op == "browse_export") {
                path = chooseExport ? chooseExport() : QString();
            }
            if (path.isEmpty()) {
                return state();
            }
            saveProfile(op, path);
        } else if (op == "piece") {
            const int i = args["index"].toInt(-1);
            require(i >= 0 && i < _profile.sources.size() && args["included"].isBool(), "Choose a source piece and included boolean");
            auto& source = _profile.sources[i];
            authored = source.worldIncluded != args["included"].toBool();
            source.worldIncluded = args["included"].toBool();
            if (!source.worldIncluded) {
                for (auto& hull : _profile.hulls) {
                    if (hull.source == source.key) {
                        authored |= hull.enabled;
                        hull.enabled = false;
                    }
                }
            }
            _dirty |= authored;
        } else if (op == "hull") {
            const int i = args["index"].toInt(-1);
            require(i >= 0 && i < _profile.hulls.size(), "Unknown hull");
            selectHull(i);
            if (args.contains("enabled")) {
                require(args["enabled"].isBool(), "Expected enabled boolean");
                authored = _profile.hulls[i].enabled != args["enabled"].toBool();
                _profile.hulls[i].enabled = args["enabled"].toBool();
                _dirty |= authored;
            }
        } else if (op == "view") {
            const std::pair<const char*, void (CollisionWorkshop::*)(bool)> views[]{ { "overlay", &CollisionWorkshop::setOverlay },
                                                                                     { "compare", &CollisionWorkshop::setCompare },
                                                                                     { "isolated", &CollisionWorkshop::setIsolated } };
            for (const auto& [key, set] : views) {
                if (!args.contains(key)) {
                    continue;
                }
                require(args[key].isBool(), "Expected view boolean");
                (this->*set)(args[key].toBool());
            }
        } else {
            require(false, "Unknown collision operation");
        }
        update(authored);
        return state();
    }

    // defaults first resets the form to ROCK's defaults (the mass stays);
    // the request may set the gap, support error and target points.
    void CollisionWorkshop::generate(const QString& operation, const QJsonObject& args)
    {
        applyGenerationSettings(operation, args);
        auto p = context(false);
        _cancel = std::make_shared<std::atomic<bool>>(false);
        auto stop = _cancel;
        _generating = true;
        _worker.setFuture(QtConcurrent::run([p = std::move(p), stop]() mutable { return bakeCollision(std::move(p), stop); }));
    }

    void CollisionWorkshop::applyGenerationSettings(const QString& operation, const QJsonObject& args)
    {
        if (operation == "defaults") {
            setGap(1);
            setSupportError(.5);
            setTargetPoints(96);
            setRadius(.01);
            setAlgorithm(CollisionAlgorithm::Rock);
        }
        if (args.contains("algorithm")) {
            CollisionAlgorithm algorithm{};
            require(parseCollisionAlgorithm(args["algorithm"].toString(), algorithm), "Unknown algorithm; use rock or concavity");
            setAlgorithm(algorithm);
        }
        struct Setting
        {
            const char* key;
            const CollisionFormField& field;
            void (CollisionWorkshop::*set)(double);
        };
        const Setting settings[]{ { "gapGame", kCollisionGap, &CollisionWorkshop::setGap },
                                  { "supportErrorGame", kCollisionSupportError, &CollisionWorkshop::setSupportError },
                                  { "targetPoints", kCollisionTargetPoints, &CollisionWorkshop::setTargetPoints },
                                  { "concavityGame", kCollisionConcavity, &CollisionWorkshop::setConcavity } };
        for (const auto& s : settings) {
            if (!args.contains(s.key)) {
                continue;
            }
            const auto v = args[s.key];
            require(v.isDouble() && std::isfinite(v.toDouble()) && v.toDouble() >= s.field.minimum && v.toDouble() <= s.field.maximum, "Invalid generation setting");
            (this->*s.set)(v.toDouble());
        }
    }

    // A saved profile opens only for the same weapon, loadout and target, and
    // only while every source piece still has the same geometry.
    void CollisionWorkshop::openProfile(const QString& path)
    {
        QFile file(path);
        require(file.size() <= 32 * 1024 * 1024 && file.open(QIODevice::ReadOnly), "Cannot read collision JSON (maximum 32 MB)");
        const auto bytes = file.readAll();
        QJsonParseError parse;
        const auto doc = QJsonDocument::fromJson(bytes, &parse);
        require(parse.error == QJsonParseError::NoError && doc.isObject(), "Invalid collision JSON");
        CollisionProfile next;
        QString error;
        require(readCollisionJson(doc.object(), next, error), error);
        const auto current = context(false);
        require(next.weapon == current.weapon && next.configuration == current.configuration && next.target == current.target,
                "Profile belongs to a different weapon, loadout or target");
        for (auto& source : next.sources) {
            auto it = std::find_if(current.sources.begin(), current.sources.end(),
                                   [&](const auto& s) { return s.key == source.key; });
            require(it != current.sources.end(), "The profile names a piece this weapon no longer shows");
            source.frame = it->frame;
            source.material = it->material;
        }
        next.referencePose["rootToWorld"] = current.referencePose["rootToWorld"];
        _baseline = _profile;
        _profile = std::move(next);
        _dirty = false;
        _lastPath = path;
    }

    // Profiles are written only inside the workspace. export-hulls writes the
    // cook input and leaves the profile unsaved.
    void CollisionWorkshop::saveProfile(const QString& operation, const QString& path)
    {
        const auto full = QFileInfo(path).absoluteFilePath();
        require(full.startsWith(QDir(studioRoot()).absolutePath() + "/", Qt::CaseInsensitive), "Save collision profiles inside the Reload Maker workspace");
        QString error;
        const auto document = operation == "export-hulls" ? collisionCookInput(_profile, error) : collisionJson(_profile);
        require(error.isEmpty(), error);
        QDir().mkpath(QFileInfo(full).absolutePath());
        QSaveFile file(full);
        const auto bytes = QJsonDocument(document).toJson();
        require(file.open(QIODevice::WriteOnly) && file.write(bytes) == bytes.size() && file.commit(), "Could not save collision profile");
        _lastPath = full;
        if (operation != "export-hulls") {
            _dirty = false;
        }
    }
}
