#include "ReloadMakerController.h"
#include "StudioWindow.h"
#include "StudioPaths.h"
#include "commands/CommandSupport.h"
#include "viewport/PartPicker.h"
#include "glview.h"

#include <QApplication>
#include <QCheckBox>
#include <QComboBox>
#include <QJsonArray>
#include <QListWidget>
#include <QMatrix4x4>
#include <QOpenGLPaintDevice>
#include <QPainter>
#include <QPushButton>
#include <QScopeGuard>
#include <QTimer>
#include <QUuid>
#include <QVector3D>
#include <QVector4D>
#include <algorithm>

namespace reloadmaker
{
    using commands::number;
    using commands::require;

    void ReloadMakerController::registerLoadoutCommands()
    {
        connect(_window->getGLView(), &GLView::paintUpdate, this, [this] { paintViewMarks(); });

        _commands.add({ "select_weapon", CommandAccess::Edit, CommandGate::WhenIdle,
                        [this](const QJsonObject& params) -> std::optional<QJsonObject> {
                            const int index = params["index"].toInt(-1);
                            require(index >= 0 && index < _weaponSelections.size(), "Unknown weapon index");
                            selectWeapon(index);
                            // A saved project may switch source; numeric indices
                            // belong to the old source until loading completes.
                            require(_loadThread || _selectedWeapon == index, "Weapon change failed: " + _lastError);
                            if (!_loadThread) {
                                rebuildWeapon();
                            }
                            return std::nullopt;
                        } });

        _commands.add({ "configure", CommandAccess::Edit, CommandGate::WhenIdle,
                        [this](const QJsonObject& params) -> std::optional<QJsonObject> {
                            configureLoadout(params);
                            return std::nullopt;
                        } });

        _commands.add({ "action", CommandAccess::Edit, CommandGate::WhenIdle,
                        [this](const QJsonObject& params) -> std::optional<QJsonObject> {
                            const int index = params["index"].toInt(-1);
                            require(index >= 0 && index < _animationActions.size(), "Invalid action index");
                            previewNativeClip(index);
                            return std::nullopt;
                        } });

        _commands.add({ "playback", CommandAccess::Edit, CommandGate::WhenIdle,
                        [this](const QJsonObject& params) -> std::optional<QJsonObject> {
                            controlPlayback(params);
                            return std::nullopt;
                        } });

        _commands.add({ "view", CommandAccess::Edit, CommandGate::WhenIdle,
                        [this](const QJsonObject& params) -> std::optional<QJsonObject> {
                            arrangeView(params);
                            return std::nullopt;
                        } });

        _commands.add({ "render", CommandAccess::Read, CommandGate::WhenIdle,
                        [this](const QJsonObject& params) -> std::optional<QJsonObject> {
                            return renderView(params);
                        } });

        _commands.add({ "picture_sheet", CommandAccess::Read, CommandGate::Always,
                        [this](const QJsonObject& params) -> std::optional<QJsonObject> { return pictureSheet(params); } });

        _commands.add({ "window_capture", CommandAccess::Read, CommandGate::WhenIdle,
                        [this](const QJsonObject&) -> std::optional<QJsonObject> { return captureWindow(); } });

        // Which installed part a click at this viewport point would select.
        _commands.add({ "part_at", CommandAccess::Read, CommandGate::WhenIdle,
                        [this](const QJsonObject& params) -> std::optional<QJsonObject> {
                            return partAtPoint(params);
                        } });

        // The same focus change as clicking a part on the model or its label.
        _commands.add({ "focus_slot", CommandAccess::Edit, CommandGate::WhenIdle,
                        [this](const QJsonObject& params) -> std::optional<QJsonObject> {
                            bool slotOk = false;
                            const auto slot = params["slot"].toString().toULongLong(&slotOk);
                            const auto* config = configuration();
                            require(slotOk && params["slot"].isString() && config && config->slot(slot),
                                    "Slot must be a decimal-string key from the current state");
                            focusSlot(slot);
                            return std::nullopt;
                        } });
    }

    // Slot and modification IDs are decimal strings because 64-bit slot keys do
    // not survive JSON numbers.
    void ReloadMakerController::configureLoadout(const QJsonObject& params)
    {
        bool slotOk = false;
        bool modOk = false;
        const auto slot = params["slot"].toString().toULongLong(&slotOk);
        const auto mod = params["mod"].toString().toUInt(&modOk);
        if (params.contains("slot")) {
            require(slotOk && modOk && params["slot"].isString() && params["mod"].isString(),
                    "Slot and modification IDs must be decimal strings from the current state");
        }
        if (params.contains("body")) {
            require(isArmsBody(params["body"].toString()), "Unknown body");
        }
        require(!params.contains("animationSet"), "Animation sets follow the equipped OMODs; select weapon parts instead.");
        if (params.contains("reloadNoExtender")) {
            require(params["reloadNoExtender"].isBool(), "reloadNoExtender must be a boolean");
            require(!params.contains("slot"), "Select parts before choosing the optional reload clip");
            require(!params["reloadNoExtender"].toBool() || !noExtenderReloadPath().isEmpty(),
                    "This animation set has no NoExtender reload");
            setGlockNoExtender(params["reloadNoExtender"].toBool());
        }
        // The rebuild below assembles the new body.
        if (params.contains("body")) {
            setArmsBody(params["body"].toString());
        }
        if (params.contains("slot")) {
            auto* config = configuration();
            require(config && config->slot(slot) &&
                    (config->selected(slot) == mod || config->select(slot, mod)), "Invalid slot or modification");
            refreshAttachments();
        }
        rebuildWeapon();
    }

    void ReloadMakerController::controlPlayback(const QJsonObject& params)
    {
        invalidateCollisions();
        require(_clip.valid(), "Load a configured animation first");
        if (params.contains("mode")) {
            const int mode = params["mode"].toInt(-1);
            require(mode == 0 || mode == 1, "Mode must be 0 fixed or 1 native");
            setWeaponFixedMotion(mode == 0);
        }
        if (params.contains("seconds")) {
            const float seconds = number(params["seconds"]);
            const float duration = _reloadWorkspaceActive && _reloadRecipe ? _reloadRecipe->duration : playbackDuration();
            require(seconds >= 0 && seconds <= duration, "Time is outside this animation");
            _playing = false;
            _playbackTimer->stop();
            if (_reloadWorkspaceActive) {
                _reloadPlaying = false;
                _reloadPreviewTimer->stop();
                seekReload(seconds);
            } else {
                _playbackTime = seconds;
                require(applyPose(seconds), _poseError);
                updateTimelineText();
            }
        }
        if (params.contains("playing")) {
            const bool playing = params["playing"].toBool();
            if (_reloadWorkspaceActive) {
                require(_reloadRecipe.has_value(), "Learn the reload first");
                setReloadPlaying(playing);
            } else if (playing != _playing) {
                togglePlayback();
            }
        }
        if (params.contains("loop")) {
            setLoopPlayback(params["loop"].toBool());
        }
    }

    // A weapon-local point given as three numbers, in the scene's space.
    Vec3f ReloadMakerController::weaponPointInWorld(const QJsonValue& value) const
    {
        const auto p = value.toArray();
        require(p.size() == 3 && _weaponBoneIndex >= 0 && _weaponBoneIndex < _globalPose.size(), "Expected a weapon-local point of three numbers");
        return composed(_globalPose[_weaponBoneIndex], Transformf{ { number(p[0]), number(p[1]), number(p[2]) } }).translation;
    }

    void ReloadMakerController::arrangeView(const QJsonObject& params)
    {
        auto* viewport = _window->getGLView();
        if (params.contains("camera")) {
            require(viewport->studioRestoreCamera(params["camera"].toObject()), "Invalid camera state");
        }
        const QString view = params["view"].toString("current");
        const QHash<QString, GLView::ViewState> views{ { "front", GLView::ViewFront }, { "back", GLView::ViewBack },
                                                       { "side", GLView::ViewRight },  { "left", GLView::ViewLeft },
                                                       { "top", GLView::ViewTop },     { "bottom", GLView::ViewBottom },
                                                       { "iso", GLView::ViewDefault } };
        if (view != "current") {
            require(views.contains(view), "Unknown camera view");
            viewport->setOrientation(views[view], false);
            if (view == "iso") {
                viewport->setRotation(-65, 0, 135);
            }
        }
        if (params.contains("rotation")) {
            const auto angles = params["rotation"].toArray();
            require(angles.size() == 3, "Rotation is three angles in degrees");
            viewport->setRotation(number(angles[0]), number(angles[1]), number(angles[2]));
        }
        if (params["fit"].toBool()) {
            require(fitWeapon(), "No weapon bounds available for Fit");
        }
        // Frames a region given by weapon-local points, such as a hand on its part.
        if (params.contains("focus")) {
            const auto points = params["focus"].toArray();
            require(points.size() >= 2 && points.size() <= 64 && _weaponBoneIndex >= 0 && _weaponBoneIndex < _globalPose.size(),
                    "Focus needs 2 to 64 weapon-local points and a loaded weapon");
            QVector<QVector3D> world;
            for (const auto value : points) {
                const auto q = weaponPointInWorld(value);
                world.append({ q.x, q.y, q.z });
            }
            require(viewport->studioFitBounds(world), "The view cannot frame these points");
        }
        if (params.contains("zoom")) {
            const float zoom = number(params["zoom"]);
            require(zoom > .05f && zoom < 20, "Zoom outside supported range");
            viewport->setZoom(zoom);
        }
    }

    // The marks of the render being captured: each is a weapon-local point shown
    // as a coloured dot with an optional label, or a line to a second point.
    // Drawn while the frame paints, as the other viewport overlays are, so the
    // camera is the one the frame was drawn with; a mark behind a mesh stays visible.
    void ReloadMakerController::paintViewMarks() const
    {
        if (_viewMarks.isEmpty() || _weaponBoneIndex < 0 || _weaponBoneIndex >= _globalPose.size()) {
            return;
        }
        auto* view = _window->getGLView();
        const auto camera = view->reloadMakerWorldToClip();
        const double width = view->width(), height = view->height(), unit = height / 100.0;
        const auto dpr = view->devicePixelRatioF();
        const auto project = [&](const QJsonValue& value, QPointF& out) {
            const auto p = value.toArray();
            if (p.size() != 3) {
                return false;
            }
            const auto world = composed(_globalPose[_weaponBoneIndex], Transformf{ { float(p[0].toDouble()), float(p[1].toDouble()), float(p[2].toDouble()) } }).translation;
            const auto clip = camera * QVector4D(world.x, world.y, world.z, 1);
            if (clip.w() <= .001f) {
                return false;
            }
            out = { (clip.x() / clip.w() + 1) * width / 2, (1 - clip.y() / clip.w()) * height / 2 };
            return true;
        };
        QOpenGLPaintDevice device(QSize(qRound(width * dpr), qRound(height * dpr)));
        device.setDevicePixelRatio(dpr);
        QPainter painter(&device);
        painter.setRenderHint(QPainter::Antialiasing);
        QFont font = painter.font();
        font.setBold(true);
        for (const auto value : _viewMarks) {
            const auto mark = value.toObject();
            QColor color(mark["color"].toString("#ffd371"));
            if (!color.isValid()) {
                color = QColor("#ffd371");
            }
            QPointF point;
            if (!project(mark["point"], point)) {
                continue;
            }
            if (mark.contains("to")) {
                QPointF end;
                if (project(mark["to"], end)) {
                    painter.setPen(QPen(QColor(0, 0, 0, 200), unit * .9));
                    painter.drawLine(point, end);
                    painter.setPen(QPen(color, unit * .5));
                    painter.drawLine(point, end);
                    point = end;
                }
            }
            const double radius = unit * std::clamp(mark["size"].toDouble(1.4), .4, 6.0);
            painter.setPen(QPen(QColor(0, 0, 0, 230), unit * .3));
            painter.setBrush(color);
            painter.drawEllipse(point, radius, radius);
            const auto label = mark["label"].toString();
            if (!label.isEmpty()) {
                font.setPixelSize(std::max(10, int(radius * (label.size() > 2 ? 1.5 : 1.9))));
                painter.setFont(font);
                // A short label sits inside its dot; a longer one beside it on a dark plate.
                if (label.size() <= 2) {
                    painter.setPen(color.lightness() > 140 ? Qt::black : Qt::white);
                    painter.drawText(QRectF(point.x() - radius, point.y() - radius, radius * 2, radius * 2), Qt::AlignCenter, label);
                } else {
                    const auto box = painter.fontMetrics().boundingRect(label).adjusted(-4, -2, 4, 2);
                    const QPointF origin(point.x() + radius + 4 - box.left(), point.y() + box.height() / 4.0);
                    painter.setPen(Qt::NoPen);
                    painter.setBrush(QColor(0, 0, 0, 190));
                    painter.drawRect(QRectF(box).translated(origin));
                    painter.setPen(Qt::white);
                    painter.drawText(origin, label);
                }
            }
        }
    }

    // Several saved pictures as one labelled grid, so a client compares them in a
    // single image. Panels are scaled to a common cell width.
    QJsonObject ReloadMakerController::pictureSheet(const QJsonObject& params) const
    {
        const auto panels = params["panels"].toArray();
        require(!panels.isEmpty() && panels.size() <= 24, "A sheet holds 1 to 24 panels");
        const int columns = std::clamp(params["columns"].toInt(2), 1, 6);
        const int cell = std::clamp(params["cellWidth"].toInt(640), 160, 1280);
        const int bar = cell / 12, gap = 6;
        QVector<QImage> images;
        int rowHeight = 0;
        for (const auto value : panels) {
            QImage image(value.toObject()["path"].toString());
            require(!image.isNull(), "Cannot read panel picture " + value.toObject()["path"].toString());
            images.append(image.scaledToWidth(cell, Qt::SmoothTransformation));
            rowHeight = std::max(rowHeight, images.last().height());
        }
        const int rows = (panels.size() + columns - 1) / columns;
        const auto title = params["title"].toString();
        const int top = title.isEmpty() ? 0 : bar + gap;
        QImage sheet(columns * cell + (columns + 1) * gap, top + rows * (rowHeight + bar + gap) + gap, QImage::Format_RGB32);
        sheet.fill(QColor(24, 26, 30));
        QPainter painter(&sheet);
        painter.setRenderHint(QPainter::Antialiasing);
        QFont font = painter.font();
        font.setBold(true);
        font.setPixelSize(bar * 7 / 10);
        painter.setFont(font);
        if (!title.isEmpty()) {
            painter.setPen(Qt::white);
            painter.drawText(QRect(gap * 2, 0, sheet.width() - gap * 4, bar + gap), Qt::AlignVCenter | Qt::AlignLeft, title);
        }
        for (int i = 0; i < images.size(); ++i) {
            const int x = gap + (i % columns) * (cell + gap), y = top + gap + (i / columns) * (rowHeight + bar + gap);
            painter.fillRect(x, y, cell, bar, QColor(255, 211, 113));
            painter.setPen(Qt::black);
            painter.drawText(QRect(x + gap, y, cell - gap * 2, bar), Qt::AlignVCenter | Qt::AlignLeft, panels[i].toObject()["label"].toString());
            painter.drawImage(x, y + bar, images[i]);
        }
        painter.end();
        const QString path = studioDataPath("evidence/sheet-" + QUuid::createUuid().toString(QUuid::WithoutBraces) + ".png");
        require(sheet.save(path), "Cannot save the picture sheet");
        return { { "path", path }, { "width", sheet.width() }, { "height", sheet.height() }, { "panels", panels.size() } };
    }

    // Renders into the evidence folder. With preserveCamera the camera returns to
    // its previous state afterwards, even when arranging the view fails.
    QJsonObject ReloadMakerController::renderView(const QJsonObject& params)
    {
        auto* viewport = _window->getGLView();
        const auto previousCamera = viewport->studioCameraState();
        const bool preserve = params["preserveCamera"].toBool();
        const auto restoreCamera = qScopeGuard([&] {
            if (preserve) {
                (void)viewport->studioRestoreCamera(previousCamera);
            }
        });
        arrangeView(params);
        const QString view = params["view"].toString("current");
        const auto captureId = QUuid::createUuid().toString(QUuid::WithoutBraces);
        const QString path = studioDataPath("evidence/view-" + captureId + "-" + view + ".png");
        const auto marks = params["marks"].toArray();
        require(marks.size() <= 256, "At most 256 marks");
        _viewMarks = marks;
        const auto clearMarks = qScopeGuard([&] { _viewMarks = {}; });
        auto image = viewport->studioCaptureFrame();
        require(!image.isNull(), "Cannot capture current native frame");
        // The caption is a bar across the top of the picture, over the editor's own tool chips.
        if (const auto caption = params["caption"].toString(); !caption.isEmpty()) {
            QPainter painter(&image);
            const double unit = image.height() / 100.0;
            QFont font = painter.font();
            font.setBold(true);
            font.setPixelSize(std::max(12, int(unit * 3.6)));
            painter.setFont(font);
            const QRectF bar(0, 0, image.width(), unit * 8);
            painter.fillRect(bar, QColor(20, 22, 26));
            painter.setPen(Qt::white);
            painter.drawText(bar.adjusted(unit, 0, -unit, 0), Qt::AlignVCenter | Qt::AlignLeft, caption);
        }
        require(image.save(path), "Cannot capture current native frame");
        auto result = commandStatus();
        result["captureId"] = captureId;
        result["documentRevision"] = double(_revision);
        result["path"] = path;
        result["seconds"] = _playbackTime;
        result["width"] = image.width();
        result["height"] = image.height();
        result["camera"] = viewport->studioCameraState();
        result["mode"] = _simulationActive ? "operate" : "source";
        result["previews"] = QJsonObject{{"holding",_holdingPreview},{"runtimeReplay",_holdingReplay},
            {"magazineFit",_magazineFitPreview},{"handPose",bool(_handPosePreview)}};
        result["magazineFit"] = magazineEditorState();
        commands::writeFileAtomically(path + ".json", QJsonDocument(result).toJson());
        return result;
    }

    // The whole window as the user sees it. A widget grab leaves the native GL
    // window blank, so its frame is drawn into the container's place.
    QJsonObject ReloadMakerController::captureWindow()
    {
        QWidget* target = QApplication::activeModalWidget();
        if (!target) target = _window;
        QImage image = target->grab().toImage();
        require(!image.isNull(), "Cannot capture the window");
        auto* container = _window->viewportContainer();
        const auto frame = _window->getGLView()->studioCaptureFrame();
        if (target == _window && !frame.isNull() && container->isVisible()) {
            QPainter painter(&image);
            painter.drawImage(QRect(container->mapTo(_window, QPoint(0, 0)), container->size()), frame);
        }
        const QString path = studioDataPath("evidence/window-" + QString::number(_revision) + ".png");
        require(image.save(path), "Cannot save the window capture");
        return { { "path", path }, { "width", target->width() }, { "height", target->height() } };
    }

    // Points are logical viewport pixels, the same space as mouse events.
    QJsonObject ReloadMakerController::partAtPoint(const QJsonObject& params)
    {
        const auto* viewport = _window->getGLView();
        require(params["x"].isDouble() && params["y"].isDouble(), "x and y viewport pixels are required");
        const float x = number(params["x"]);
        const float y = number(params["y"]);
        require(x >= 0 && y >= 0 && x < viewport->width() && y < viewport->height(), "Point is outside the viewport");
        QJsonObject result{ { "slot", QJsonValue::Null }, { "width", viewport->width() }, { "height", viewport->height() } };
        const auto* config = configuration();
        const auto slot = _partPicker->partAt(QPointF(x, y));
        const auto* entry = slot && config ? config->slot(*slot) : nullptr;
        if (!entry) {
            return result;
        }
        const auto* mod = config->mod(config->selected(*slot));
        result["slot"] = QString::number(*slot);
        result["label"] = entry->label;
        result["part"] = mod ? (mod->name.isEmpty() ? mod->editorId : mod->name) : QString();
        result["editorId"] = mod ? mod->editorId : QString();
        return result;
    }
}
