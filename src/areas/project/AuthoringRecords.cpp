// Authoring records: what a weapon's authoring session keeps per reload
// configuration (checklist confirmations, magazine guide/setup/reference,
// operating setup, collision drafts), the weapon's reload parts that key
// them, and the behaviour preset assigned to the weapon.
#include "ReloadMakerController.h"
#include "ReloadTemplates.h"
#include "commands/CommandSupport.h"
#include "BehaviorAssignments.h"

#include <algorithm>
#include <optional>

namespace reloadmaker
{
    using commands::require;

    namespace
    {
        constexpr auto kWholeWeapon = "weapon";

        int roleOrder(const QJsonValue& value)
        {
            const auto role = value.toObject()["role"].toString();
            return role == QLatin1String("receiver") ? 0 : role == QLatin1String("operatingPart") ? 1 : 2;
        }
    }

    QString ReloadMakerController::assignedTemplate() const
    {
        if(!currentWeapon())return {};
        const auto parts=behaviorParts();
        return resolvedBehaviorTemplate(_reloadTemplateAssignments,poseRecordKey(currentWeapon()->formId).toLower(),parts.first,parts.second);
    }

    QPair<QStringList,QString> ReloadMakerController::behaviorParts() const
    {
        QStringList moving;QString magazine="weapon";
        if(currentWeapon()&&configuration()&&currentPlugin()) {
            const auto choices=configuration()->savedChoices(*currentPlugin());
            // Recorded axes avoid recursively asking the rig for its behavior.
            for(const auto& value:_reloadAxes[poseRecordKey(currentWeapon()->formId).toLower()].toArray()) {
                const auto axis=value.toObject();const auto part=choices[axis["slot"].toString()].toString().toLower();
                if(axis["role"]=="magazine")magazine=part.isEmpty()?QStringLiteral("weapon"):part;
                else if(!part.isEmpty())moving.append(part);
            }
        }
        if(moving.isEmpty())moving.append("weapon");
        return {moving,magazine};
    }

    QString ReloadMakerController::configurationRoleName(ConfigurationRole role)
    {
        switch (role) {
        case ConfigurationRole::Receiver: return QStringLiteral("receiver");
        case ConfigurationRole::OperatingPart: return QStringLiteral("operatingPart");
        case ConfigurationRole::Magazine: break;
        }
        return QStringLiteral("magazine");
    }

    // "<weapon>/receiver=<record>;operatingPart=<record>;magazine=<record>",
    // from the weapon's recorded reload parts and the parts selected in them
    // ("none" for an empty slot). Every other part (barrel, grip, sights,
    // underbarrel, muzzle) leaves the key alone (owner, 2026-10-05). Before
    // Operate has recorded the parts, the weapon has one record, "<weapon>/weapon".
    namespace
    {
        bool magazineField(const QString& field)
        {
            return field == QLatin1String("magazineGuide") || field == QLatin1String("magazineReference") ||
                   field == QLatin1String("magazineSetup") || field == QLatin1String("collisionDrafts");
        }

        bool partField(const QString& field)
        {
            return magazineField(field) || field == QLatin1String("operating");
        }

        // Operating ownership follows the declared roles, even when a fixed
        // feed's loading point is also owned by the receiver.
        QStringList movingChoices(const QString& suffix)
        {
            QStringList values;
            for (const auto& part : suffix.split(';')) {
                if (part.contains('=') && !part.startsWith(QLatin1String("magazine=")) && part.section('=',1)!=QLatin1String("none")) {
                    values.append(part.section('=', 1).toLower());
                }
            }
            return values;
        }

        // One field from one weapon's roadmaps: the part's own scope, then the
        // weapon's anchor of that kind, then any part of that kind authored.
        PlanField resolveIn(const QJsonObject& roadmaps, const QString& weapon, const QString& field, const QString& magazine,
                            const QStringList& moving, bool own)
        {
            const bool forMagazine = magazineField(field);
            const QString prefix = weapon + '/';
            const auto found = [&](const QString& key, PlanSource source) -> std::optional<PlanField> {
                const auto plan = roadmaps.value(key).toObject();
                if (!plan.contains(field)) {
                    return std::nullopt;
                }
                return PlanField{ plan[field], source, key };
            };
            if (auto f = found(prefix + (forMagazine ? "@magazine:" + magazine : "@moving:" + moving.join(';')), own ? PlanSource::Own : PlanSource::Sibling)) {
                return *f;
            }
            // Reference, presentation and collision drafts name nodes inside
            // one magazine model: they never cross to another magazine.
            if (forMagazine && field != QLatin1String("magazineGuide")) {
                return {};
            }
            const auto source = own ? PlanSource::Anchor : PlanSource::Sibling;
            const auto anchor = roadmaps.value(prefix + "@anchors").toObject().value(forMagazine ? "magazineScope" : "movingScope").toString();
            if (auto f = found(anchor, source)) {
                return *f;
            }
            QStringList keys = roadmaps.keys();
            keys.sort();
            const QString kind = prefix + (forMagazine ? "@magazine:" : "@moving:");
            for (const auto& key : keys) {
                if (key.startsWith(kind)) {
                    if (auto f = found(key, source)) {
                        return *f;
                    }
                }
            }
            return {};
        }

        QString sourceName(PlanSource source)
        {
            switch (source) {
            case PlanSource::Own: return QStringLiteral("own");
            case PlanSource::Anchor: return QStringLiteral("anchor");
            case PlanSource::Sibling: return QStringLiteral("sibling");
            case PlanSource::None: break;
            }
            return QStringLiteral("none");
        }
    }

    // The magazine part this configuration loads, from its configuration key.
    // Its node may live in another part's model (the AMR, M16A2E3 and 1911
    // drum magazines ride the receiver's WeaponMagazine), but authoring
    // belongs to the magazine part. A magazine built into the weapon is the
    // owner of the reload's magazine track, else "weapon". Never read through
    // simulationMagazineAnchor, which itself reads the plan.
    QString ReloadMakerController::magazineScopeRecord() const
    {
        // A fixed bolt-action feed loads through its receiver. An installed
        // clip OMOD may remain selected while that receiver supplies a different
        // single-round carrier, so it cannot own every receiver's loading path.
        const auto* preset=checklistPreset();
        const bool receiverFeed=preset&&std::any_of(preset->model.mechanisms.cbegin(),preset->model.mechanisms.cend(),[](const auto& action){return action.kind==MechanismKind::TurnBolt;});
        if(unitLoadingConfiguration()&&receiverFeed) {
            if(const auto* receiver=reloadReceiverObjectMod())return poseRecordKey(receiver->formId).toLower();
        }
        for (const auto& part : authoringRecordKey().section('/', 1).split(';')) {
            if (part.startsWith(QLatin1String("magazine=")) && part.section('=', 1) != QLatin1String("none")) {
                return part.section('=', 1).toLower();
            }
        }
        if (_reloadRecipe && _simulationRig && _simulationRig->magazine >= 0 && _simulationRig->magazine < _reloadRecipe->tracks.size()) {
            const auto owner = _reloadRecipe->tracks[_simulationRig->magazine].anchor.ownerRecord;
            if (!owner.isEmpty()) {
                return owner.toLower();
            }
        }
        if (_reloadRecipe) {
            QString owner;
            for (const auto& track : _reloadRecipe->tracks) {
                if (track.anchor.anchorName.compare("WeaponMagazine", Qt::CaseInsensitive) == 0 && !track.anchor.ownerRecord.isEmpty()) {
                    if (!owner.isEmpty() && owner.compare(track.anchor.ownerRecord, Qt::CaseInsensitive) != 0) {
                        owner.clear();
                        break;
                    }
                    owner = track.anchor.ownerRecord;
                }
            }
            if (!owner.isEmpty()) {
                return owner.toLower();
            }
        }
        return QStringLiteral("weapon");
    }

    // The moving parts of this configuration: every part record it names
    // except the magazine, or "weapon" when it names none.
    QStringList ReloadMakerController::movingScopeRecords() const
    {
        QStringList moving=movingChoices(authoringRecordKey().section('/',1));
        if (moving.isEmpty()) {
            moving.append(QStringLiteral("weapon"));
        }
        return moving;
    }

    QString ReloadMakerController::planScopeKey(const QString& field) const
    {
        const auto key = authoringRecordKey();
        if (key.isEmpty() || !partField(field)) {
            return key;
        }
        const auto weapon = key.section('/', 0, 0);
        return magazineField(field) ? weapon + "/@magazine:" + magazineScopeRecord() : weapon + "/@moving:" + movingScopeRecords().join(';');
    }

    PlanField ReloadMakerController::authoringPlanField(const QString& field) const
    {
        const auto key = authoringRecordKey();
        if (key.isEmpty()) {
            return {};
        }
        if (!partField(field)) {
            const auto plan = _authoringRecords.value(key).toObject();
            return plan.contains(field) ? PlanField{ plan[field], PlanSource::Own, key } : PlanField{};
        }
        const auto weapon = key.section('/', 0, 0);
        const auto magazine = magazineScopeRecord();
        const auto moving = movingScopeRecords();
        const auto own=resolveIn(_authoringRecords,weapon,field,magazine,moving,true);
        if(own.source==PlanSource::Own)return own;
        // Prefer a sibling built from this exact operating model over the
        // weapon's general anchor (for example straight versus bent bolt rigs).
        if(field==QLatin1String("operating")&&_reloadRecipe) {
            const auto prefix=weapon+"/@moving:";
            for(auto it=_authoringRecords.begin();it!=_authoringRecords.end();++it) {
                if(!it.key().startsWith(prefix))continue;
                const auto value=it.value().toObject()[field].toObject();
                const auto anchors=value["trackAnchors"].toArray();const int index=value["operating"].toInt(-1);
                if(index<0||index>=anchors.size())continue;
                const int mapped=mapRecipeTrack(anchors,*_reloadRecipe,index);if(mapped<0)continue;
                const auto model=anchors[index].toObject()["model"].toString().replace('\\','/');
                if(!model.isEmpty()&&model.compare(QString(_reloadRecipe->tracks[mapped].anchor.ownerModel).replace('\\','/'),Qt::CaseInsensitive)==0)
                    return {value,PlanSource::Anchor,it.key()};
            }
        }
        if(own.source!=PlanSource::None)return own;
        // Embedded cells can have many stat-only receiver records for one
        // physical model. Reuse its authored presentation just as track
        // remapping reuses that model's operating motion.
        if (field == QLatin1String("magazineSetup") && _reloadRecipe) {
            const int full = _simulationRig?_simulationRig->magazine:detectMagazineTrack(*_reloadRecipe);
            if (full >= 0 && full < _reloadRecipe->tracks.size()) {
                const auto& current = _reloadRecipe->tracks[full].anchor;
                const auto prefix = weapon + "/@magazine:";
                for (auto it = _authoringRecords.begin(); it != _authoringRecords.end(); ++it) {
                    if (!it.key().startsWith(prefix)) continue;
                    const auto value = it.value().toObject()[field].toObject();
                    const auto anchors = value["trackAnchors"].toArray();
                    const int empty = value["emptyTrack"].toInt(-1);
                    if (empty < 0 || empty >= anchors.size()) continue;
                    const auto source = anchors[empty].toObject();
                    auto model = source["model"].toString().replace('\\', '/');
                    if (!model.isEmpty() && model.compare(QString(current.ownerModel).replace('\\', '/'), Qt::CaseInsensitive) == 0 &&
                        mapRecipeTrack(anchors, *_reloadRecipe, empty) >= 0)
                        return { value, PlanSource::Anchor, it.key() };
                }
            }
        }
        for (const auto& [record, roadmaps] : _siblingRoadmaps) {
            if (auto shared = resolveIn(roadmaps, record, field, magazine, moving, false); shared.source != PlanSource::None) {
                return shared;
            }
        }
        return {};
    }

    // The configuration's resolved plan: its checklist skips and every part
    // field along its chain.
    QJsonObject ReloadMakerController::authoringPlan() const
    {
        const auto key = authoringRecordKey();
        if (key.isEmpty()) {
            return {};
        }
        QJsonObject plan;
        const auto own = _authoringRecords.value(key).toObject();
        for (auto it = own.begin(); it != own.end(); ++it) {
            if (!partField(it.key())) {
                plan.insert(it.key(), it.value());
            }
        }
        for (const auto* field : { "operating", "magazineGuide", "magazineReference", "magazineSetup", "collisionDrafts" }) {
            const auto resolved = authoringPlanField(QLatin1String(field));
            if (resolved.source != PlanSource::None) {
                plan.insert(QLatin1String(field), resolved.value);
            }
        }
        return plan;
    }

    // Where each part field comes from, for the UI and MCP state.
    QJsonObject ReloadMakerController::authoringPlanSources() const
    {
        QJsonObject sources;
        for (const auto* field : { "operating", "magazineGuide", "magazineReference", "magazineSetup", "collisionDrafts" }) {
            const auto resolved = authoringPlanField(QLatin1String(field));
            sources.insert(QLatin1String(field), QJsonObject{ { "source", sourceName(resolved.source) }, { "from", resolved.from } });
        }
        return sources;
    }

    void ReloadMakerController::writePlanField(const QString& field, const QJsonValue& value)
    {
        const auto scope = planScopeKey(field);
        if (scope.isEmpty()) {
            return;
        }
        auto plan = _authoringRecords.value(scope).toObject();
        if (value.isUndefined()) {
            plan.remove(field);
        } else {
            plan[field] = value;
            const auto anchorsKey = scope.section('/', 0, 0) + "/@anchors";
            auto anchors = _authoringRecords.value(anchorsKey).toObject();
            const auto kind = magazineField(field) ? QStringLiteral("magazineScope") : QStringLiteral("movingScope");
            if (!anchors.contains(kind)) {
                anchors[kind] = scope;
                _authoringRecords[anchorsKey] = anchors;
            }
            if(field==QLatin1String("magazineGuide")&&
                !_authoringRecords.value(anchors.value(kind).toString()).toObject().contains(field)) {
                // A collision/presentation may have established magazineScope
                // before any guide existed. Pin the previously inherited guide
                // before adding a new explicit override, otherwise JSON key order
                // silently changes every other magazine's fallback.
                QString guideOwner;
                const auto prefix=scope.section("/@magazine:",0,0)+QStringLiteral("/@magazine:");
                for(auto it=_authoringRecords.constBegin();it!=_authoringRecords.constEnd();++it)
                    if(it.key().startsWith(prefix)&&it.value().toObject().contains(field)){guideOwner=it.key();break;}
                anchors[kind]=guideOwner.isEmpty()?scope:guideOwner;
                _authoringRecords[anchorsKey]=anchors;
            }
        }
        if (plan.isEmpty()) {
            _authoringRecords.remove(scope);
        } else {
            _authoringRecords[scope] = plan;
        }
    }

    QString ReloadMakerController::authoringRecordKey() const
    {
        if (!currentWeapon() || !configuration()) {
            return {};
        }
        const auto weapon = poseRecordKey(currentWeapon()->formId).toLower();
        const auto axes = _reloadAxes.value(weapon).toArray();
        if (axes.isEmpty()) {
            return weapon + '/' + kWholeWeapon;
        }
        const auto choices = configuration()->savedChoices(*currentPlugin());
        QStringList parts;
        for (const auto& value : axes) {
            const auto axis = value.toObject();
            const auto choice = choices.value(axis["slot"].toString()).toString();
            parts.append(axis["role"].toString() + '=' + (choice.isEmpty() ? QStringLiteral("none") : choice));
        }
        return weapon + '/' + parts.join(';');
    }

    // The reload parts Operate's rig shows: the receiver, the operating part
    // when its slot is separate, and the magazine, each once.
    ReloadMakerController::ReloadAxes ReloadMakerController::liveReloadAxes() const
    {
        ReloadAxes axes;
        if (!currentWeapon() || !configuration()) {
            return axes;
        }
        const auto slotOf = [this](const QString& record) -> SlotKey {
            for (const auto* mod : configuredObjectMods()) {
                if (poseRecordKey(mod->formId).compare(record, Qt::CaseInsensitive) == 0) {
                    return configurationSlotKey(*mod);
                }
            }
            return 0;
        };
        const auto add = [&](ConfigurationRole role, SlotKey slot) {
            if (slot && std::none_of(axes.cbegin(), axes.cend(), [&](const auto& axis) { return axis.second == slot; })) {
                axes.append({ role, slot });
            }
        };
        if (const auto* receiver = reloadReceiverObjectMod()) {
            add(ConfigurationRole::Receiver, configurationSlotKey(*receiver));
        }
        if (const int anchor = operatingAnchor(); anchor >= 0) {
            add(ConfigurationRole::OperatingPart, slotOf(_handPoseAnchors[anchor].description.ownerRecord));
        }
        add(ConfigurationRole::Magazine, slotOf(simulationMagazineAnchor().ownerRecord));
        return axes;
    }

    // The recorded reload parts this configuration has, or Operate's live
    // reading before any are recorded.
    ReloadMakerController::ReloadAxes ReloadMakerController::reloadAxes() const
    {
        if (!currentWeapon() || !configuration()) {
            return {};
        }
        const auto recorded = _reloadAxes.value(poseRecordKey(currentWeapon()->formId).toLower()).toArray();
        if (recorded.isEmpty()) {
            return liveReloadAxes();
        }
        ReloadAxes axes;
        const auto& entries = configuration()->entries();
        for (const auto& value : recorded) {
            const auto axis = value.toObject();
            const auto slot = std::find_if(entries.cbegin(), entries.cend(), [&](const auto& entry) {
                return configuration()->slotId(*currentPlugin(), entry) == axis["slot"].toString();
            });
            if (slot == entries.cend()) {
                continue;
            }
            for (const auto role : { ConfigurationRole::Receiver, ConfigurationRole::OperatingPart, ConfigurationRole::Magazine }) {
                if (configurationRoleName(role) == axis["role"].toString()) {
                    axes.append({ role, slot->key });
                }
            }
        }
        return axes;
    }

    // Called when Operate's rig is ready. The weapon's first rig records its
    // reload parts. A later rig adds a part only when it was built from an
    // authored operating setup (the setup named the magazine or a separate
    // operating part), and never removes one, so a configuration Operate
    // reads from the preset alone cannot merge records. Records move to their
    // keys under the new parts; a part a record did not name takes this
    // configuration's choice.
    void ReloadMakerController::recordReloadAxes()
    {
        if (!currentWeapon() || !configuration()) {
            return;
        }
        const auto weapon = poseRecordKey(currentWeapon()->formId).toLower();
        const auto recorded = _reloadAxes.value(weapon).toArray();
        if (!recorded.isEmpty() && !authoringPlan().contains("operating")) {
            return;
        }
        QJsonArray next = recorded;
        for (const auto& [role, slot] : liveReloadAxes()) {
            const auto* entry = configuration()->slot(slot);
            if (!entry) {
                continue;
            }
            const auto id = configuration()->slotId(*currentPlugin(), *entry);
            const auto name = configurationRoleName(role);
            if (std::none_of(next.cbegin(), next.cend(), [&](const QJsonValue& value) {
                    const auto axis = value.toObject();
                    return axis["role"].toString() == name || axis["slot"].toString() == id;
                })) {
                next.append(QJsonObject{ { "role", name }, { "slot", id } });
            }
        }
        if (next.size() == recorded.size()) {
            return;
        }
        QVector<QJsonValue> ordered(next.cbegin(), next.cend());
        std::stable_sort(ordered.begin(), ordered.end(), [](const QJsonValue& a, const QJsonValue& b) { return roleOrder(a) < roleOrder(b); });
        next = {};
        for (const auto& value : ordered) {
            next.append(value);
        }
        const auto current = authoringRecordKey();
        const auto choices = configuration()->savedChoices(*currentPlugin());
        _reloadAxes[weapon] = next;
        const QString prefix = weapon + '/';
        QJsonObject moved;
        QStringList previous;
        for (auto it = _authoringRecords.constBegin(); it != _authoringRecords.constEnd(); ++it) {
            const auto suffix = it.key().mid(prefix.size());
            if (!it.key().startsWith(prefix) || suffix.startsWith('@') || (suffix != QLatin1String(kWholeWeapon) && !suffix.contains('='))) {
                continue;
            }
            QHash<QString, QString> named;
            if (suffix != QLatin1String(kWholeWeapon)) {
                for (const auto& part : suffix.split(';')) {
                    named.insert(part.section('=', 0, 0), part.section('=', 1));
                }
            }
            QStringList parts;
            for (const auto& value : next) {
                const auto axis = value.toObject();
                const auto role = axis["role"].toString();
                const auto choice = named.value(role, choices.value(axis["slot"].toString()).toString());
                parts.append(role + '=' + (choice.isEmpty() ? QStringLiteral("none") : choice));
            }
            const auto key = prefix + parts.join(';');
            if (!moved.contains(key) || it.key() == current) {
                moved.insert(key, it.value());
            }
            previous.append(it.key());
        }
        for (const auto& key : previous) {
            _authoringRecords.remove(key);
        }
        for (auto it = moved.constBegin(); it != moved.constEnd(); ++it) {
            _authoringRecords.insert(it.key(), it.value());
        }
        _presetProposal = {};
    }

    void ReloadMakerController::assignReloadPreset(const QString& id,const QString& scope)
    {
        require(!authoringRecordKey().isEmpty(), "Load a weapon first");
        const auto mechanism = templateMechanism(id);
        require(scope=="weapon"||scope=="assembly"||scope=="feed","Choose weapon, assembly or feed scope");
        require(mechanism.has_value()||(id.isEmpty()&&scope!="weapon"), "Choose one of the supported behavior templates");
        const auto weapon=poseRecordKey(currentWeapon()->formId).toLower();
        if(scope!="weapon")require(!_reloadAxes[weapon].toArray().isEmpty(),"Prepare the operating assembly before assigning its behavior");
        const auto parts=behaviorParts();
        const auto key=scope=="weapon"?weapon:behaviorAssignmentKey(weapon,parts.first,scope=="feed"?parts.second:QString());
        stopSimulation();
        if(!id.isEmpty()&&_reloadTemplateAssignments[weapon].toString().isEmpty())_reloadTemplateAssignments[weapon]=id;
        if(id.isEmpty())_reloadTemplateAssignments.remove(key);else _reloadTemplateAssignments[key]=id;
        _assignedMechanism = templateMechanism(assignedTemplate());
        refreshHeader();
        noteDocumentChange(QStringLiteral("reload preset"));
        prepareSimulation();
    }
}
