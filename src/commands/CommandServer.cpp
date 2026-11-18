#include "ReloadMakerController.h"
#include "StudioWindow.h"
#include "StudioPaths.h"
#include "commands/CommandSupport.h"
#include "glview.h"

#include <QCoreApplication>
#include <QJsonDocument>
#include <QLocalServer>
#include <QLocalSocket>
#include <QScopedValueRollback>
#include <QStatusBar>
#include <QThread>
#include <QTimer>

#include <memory>

namespace reloadmaker
{
    using commands::require;

    namespace
    {
        // A single request line may not exceed this; larger input aborts the client.
        constexpr qsizetype kMaximumRequestBytes = 4 * 1024 * 1024;

        QString commandSession()
        {
            return QCoreApplication::instance()->property("commandSession").toString();
        }
    }

    void ReloadMakerController::registerCommands()
    {
        registerDocumentCommands();
        registerSourceCommands();
        registerLoadoutCommands();
        registerSimulationCommands();
        registerHandCommands();
        registerMagazineCommands();
        registerOperatingCommands();
        registerReloadCommands();
        registerSoundCommands();
        registerGrabLocationCommands();
        registerCollisionCommands();
        registerScopeCommands();
        registerPackCommands();
        registerAnnotationCommands();
    }

    void ReloadMakerController::startCommandServer()
    {
        registerCommands();
        // Only the UI thread owns jobs. Retain bounded terminal results so a
        // caller timing out cannot accidentally observe a different job.
        connect(this, &ReloadMakerController::packJobChanged, this, [this] {
            const auto id = _packJob["id"].toString();
            if (id.isEmpty()) return;
            if (!_packJobHistory.contains(id)) _packJobOrder.append(id);
            _packJobHistory[id] = _packJob;
            while (_packJobOrder.size() > 32) _packJobHistory.remove(_packJobOrder.takeFirst());
        });
        auto* server = new QLocalServer(this);
        server->setSocketOptions(QLocalServer::UserAccessOption);
        const auto pipe = QCoreApplication::instance()->property("commandPipe").toString();
        const auto session = commandSession();
        require(!pipe.isEmpty() && !session.isEmpty() && server->listen(pipe),
                "Another Reload Maker owns this workspace's command pipe");
        const QJsonObject endpoint{ { "version", 1 }, { "pipe", pipe }, { "session", session },
                                    { "workspace", studioRoot() },
                                    { "pid", double(QCoreApplication::applicationPid()) } };
        commands::writeFileAtomically(studioDataPath("local/settings/endpoint.json"), QJsonDocument(endpoint).toJson());
        connect(server, &QLocalServer::newConnection, this, [this, server] {
            while (server->hasPendingConnections()) {
                acceptCommandClient(server->nextPendingConnection());
            }
        });
    }

    // UI edits and command edits share one generation, so clients read current
    // state instead of reusing selections from an earlier scene. Commands and
    // the setters behind edits and selections advance it; display toggles
    // (arms, loop, mute, search, pages, preview modes) and timer-driven view
    // refreshes do not, because no edit depends on them.
    void ReloadMakerController::noteDocumentChange(const QString& source)
    {
        if (_viewRefresh) {
            return;
        }
        ++_revision;
        if (!_executingCommand) {
            _revisionChangedOutside = source;
        }
        if (!_modifiedRefreshQueued) {
            _modifiedRefreshQueued = true;
            QTimer::singleShot(0, this, [this] {
                _modifiedRefreshQueued = false;
                refreshDocumentModified();
            });
        }
    }

    QString ReloadMakerController::staleRevisionMessage() const
    {
        const QString message = QStringLiteral("Stale document revision; read state again");
        if (_revision == _revisionAfterCommand) {
            return message;
        }
        const QString source = _revisionChangedOutside.isEmpty() ? QStringLiteral("an unattributed view or viewport change")
                                                                  : _revisionChangedOutside;
        return message + QStringLiteral(" (changed outside a command by %1)").arg(source);
    }

    // Requests are newline-delimited JSON objects; every request gets exactly one
    // newline-delimited response in order.
    void ReloadMakerController::acceptCommandClient(QLocalSocket* socket)
    {
        socket->setParent(this);
        auto pending = std::make_shared<QByteArray>();
        connect(socket, &QLocalSocket::readyRead, this, [this, socket, pending] {
            pending->append(socket->readAll());
            if (pending->size() > kMaximumRequestBytes) {
                socket->abort();
                return;
            }
            qsizetype newline;
            while ((newline = pending->indexOf('\n')) >= 0) {
                const auto line = pending->left(newline);
                pending->remove(0, newline + 1);
                socket->write(QJsonDocument(answerCommand(line)).toJson(QJsonDocument::Compact) + '\n');
                socket->flush();
            }
        });
        connect(socket, &QLocalSocket::disconnected, socket, &QObject::deleteLater);
    }

    QJsonObject ReloadMakerController::answerCommand(const QByteArray& line)
    {
        QJsonObject response;
        try {
            QJsonParseError error;
            const auto document = QJsonDocument::fromJson(line, &error);
            require(error.error == QJsonParseError::NoError && document.isObject(), "Invalid command JSON");
            response = executeCommand(document.object());
            response["ok"] = true;
        } catch (const std::exception& error) {
            response = { { "ok", false }, { "error", QString::fromUtf8(error.what()) } };
        }
        // The caller now holds this revision; later changes happen outside commands.
        _revisionAfterCommand = _revision;
        _revisionChangedOutside.clear();
        // Saved mechanisms/recipes own their revision field. Do not overwrite it
        // with the live document generation.
        response["documentRevision"] = double(_revision);
        response["session"] = commandSession();
        if (!response.contains("revision")) {
            response["revision"] = double(_revision);
        }
        return response;
    }

    QJsonObject ReloadMakerController::executeCommand(const QJsonObject& request)
    {
        const QString method = request["method"].toString();
        const auto params = request["params"].toObject();
        if (request.contains("expectedSession")) {
            require(request["expectedSession"].toString() == commandSession(),
                    "Editor session changed; read state before editing");
        }
        const auto* command = _commands.find(method);
        require(command != nullptr, "Unknown studio command: " + method);
        const bool stale = request.contains("expectedRevision") && request["expectedRevision"].toDouble() != double(_revision);
        if (command->access == CommandAccess::Edit && stale) {
            require(false, staleRevisionMessage());
        }
        const QScopedValueRollback<bool> executing(_executingCommand, true);
        if (command->gate == CommandGate::WhenIdle) {
            requireIdle();
            _lastError.clear();
        }
        if (auto response = command->run(params)) {
            return *response;
        }
        require(_lastError.isEmpty(), _lastError);
        ++_revision;
        return request["response"].toString() == "summary" ? commandStatus() : studioState();
    }

    void ReloadMakerController::requireIdle() const
    {
        require(!_loadThread && !_reloadCapturing && !_rebuildTimer->isActive(),
                "Source loading, assembly or reload capture is in progress; read state or cancel");
        require(!_saveAll && !espBusy(), "The project is being saved; read pack_job or cancel it");
    }

    QJsonObject ReloadMakerController::commandStatus() const
    {
        QJsonObject selection;
        if (const auto* config = configuration()) for (const auto& slot : config->entries())
            selection[QString::number(slot.key)] = poseRecordKey(config->selected(slot.key));
        return {{"busy", assemblyBusy() || espBusy() || bool(_saveAll) || !_pendingSession.isEmpty()},
                {"jobRunning", bool(_packProcess) || espBusy() || bool(_saveAll)},
                {"jobId", _packJob["id"]}, {"weapon", weaponRecordKey()},
                {"application", QCoreApplication::applicationDirPath()},
                {"selection", selection}, {"workspace", _workspace},
                {"hand", _interactionHand == PoseHand::Left ? "left" : "right"},
                {"lastError", _lastError}};
    }

    QJsonObject ReloadMakerController::studioState(const QStringList& fields) const
    {
        const auto wanted = [&](const char* field) { return fields.isEmpty() || fields.contains(QLatin1String(field)); };
        QJsonArray weapons;
        if (wanted("weapons")) for (int i = 0; i < _weaponSelections.size(); ++i) {
            const auto& selection = _weaponSelections[i];
            const auto& plugin = _plugins[selection.pluginIndex];
            const auto& weapon = plugin.weapons[selection.weaponIndex];
            weapons.append(QJsonObject{ { "index", i }, { "name", weapon.name }, { "editorId", weapon.editorId },
                                        { "formId", QString::number(weapon.formId, 16) },
                                        { "plugin", plugin.fileName } });
        }
        QJsonArray actions;
        if (wanted("actions")) for (int i = 0; i < _animationActions.size(); ++i) {
            actions.append(QJsonObject{ { "index", i }, { "name", _animationActions[i].label },
                                        { "path", _animationActions[i].resourcePath } });
        }
        QJsonArray configurationSlots;
        if (const auto* config = configuration(); config && wanted("slots")) {
            for (const auto& slot : config->entries()) {
                QJsonArray choices;
                for (auto id : slot.choices) {
                    if (const auto* mod = config->mod(id)) {
                        choices.append(QJsonObject{ { "id", QString::number(id) }, { "record", poseRecordKey(id) },
                                                    { "name", mod->name }, { "editorId", mod->editorId },
                                                    { "model", mod->modelPath } });
                    }
                }
                configurationSlots.append(QJsonObject{ { "key", QString::number(slot.key) }, { "name", slot.label },
                                                       { "selected", QString::number(config->selected(slot.key)) },
                                                       { "choices", choices } });
            }
        }
        QJsonArray anchors;
        if (wanted("anchors")) for (int i = 0; i < _handPoseAnchors.size(); ++i) {
            anchors.append(QJsonObject{ { "index", i }, { "name", _handPoseAnchors[i].label },
                                        { "kind", _handPoseAnchors[i].description.anchorKind } });
        }
        QJsonArray poses;
        if (wanted("handPoses")) for (const auto& pose : _savedHandPoses) {
            poses.append(QJsonObject{ { "id", pose.id }, { "name", pose.name },
                                      { "hand", pose.hand == PoseHand::Left ? "left" : "right" } });
        }
        QJsonArray grips;
        if (wanted("partGrips")) for (const auto& grip : _partGrips) {
            grips.append(QJsonObject{ { "id", grip.pose.id }, { "name", grip.pose.name },
                                      { "part", grip.pose.placement.anchorName } });
        }
        QJsonObject magazineGrabs;
        if (wanted("magazineGrabGrips")) for (auto it = _magazineGrabGrips.cbegin(); it != _magazineGrabGrips.cend(); ++it) {
            magazineGrabs[it.key()] = it.value();
        }
        const bool busy = bool(_loadThread) || _reloadCapturing || _reloadCaptureQueued || _rebuildTimer->isActive() || espBusy();
        QJsonObject result{ { "revision", double(_revision) },
                 { "session", commandSession() },
                 { "busy", busy },
                 { "loading", bool(_loadThread) },
                 { "capturing", _reloadCapturing },
                 { "source", _package.sourcePath },
                 { "gameData", _gameData },
                 { "library", _libraryMode },
                 { "workspace", _workspace },
                 { "magazineStep", magazineStep() ? int(*magazineStep()) : -1 },
                 { "weaponIndex", _selectedWeapon },
                 { "weapons", weapons },
                 { "slots", configurationSlots },
                 { "focusedSlot", QString::number(_focusedSlot) },
                 { "body", _armsBody },
                 { "animationSource", animationSourceIdentity() },
                 { "actions", actions },
                 { "action", _activeAction },
                 { "animation", _activeAnimationPath },
                 { "seconds", _playbackTime },
                 { "duration", playbackDuration() },
                 { "playing", _playing || _reloadPlaying },
                 { "motionMode", _weaponFixedMotion ? 0 : 1 },
                 { "anchors", anchors },
                 { "interaction", wanted("interaction") ? interactionGripState() : QJsonObject{} },
                 { "handPoses", poses },
                 { "partGrips", grips },
                 { "magazineGrabGrips", magazineGrabs },
                 { "magazineEditor", wanted("magazineEditor") ? magazineEditorState() : QJsonObject{} },
                 { "recipeTracks", _reloadRecipe ? int(_reloadRecipe->tracks.size()) : 0 },
                 { "status", _window->statusBar()->currentMessage() },
                 { "lastError", _lastError },
                 { "project", _esp ? QJsonObject{ { "plugin", _esp->plugin }, { "base", _esp->base } } : QJsonObject{} },
                 { "modified", espDirty() },
                 { "saving", espBusy() },
                 { "camera", _window->getGLView()->studioCameraState() },
                 { "nativeApplicationShell", false } };
        if (!fields.isEmpty()) for (auto it = result.begin(); it != result.end();) {
            if (!fields.contains(it.key()) && it.key() != "revision" && it.key() != "session" && it.key() != "busy") it = result.erase(it);
            else ++it;
        }
        return result;
    }
}
