#pragma once
#include <QJsonObject>
#include <QJsonArray>
#include <QStringList>
#include "BehaviorAssignments.h"

namespace reloadmaker {
struct CaptureOwners {
    QString magazine; // Actual captured object, which can belong to the receiver.
    QString magazineScope; // Part that owns loading-path authoring.
    QStringList movingModels;
};
// Readable authoring dependencies, not a hash or a readiness certificate.
// Compare the resolved values so an overridden default does not recapture
// a magazine that has its own fit. Unfinished edits are saved, not exported.
inline QJsonObject captureInputs(QJsonObject record, const QString& weapon,
    const QString& magazine, const QStringList& moving, const CaptureOwners& owners = {}) {
    const auto physicalMagazine=owners.magazine.isEmpty()?magazine:owners.magazine;
    const auto magazineScope=owners.magazineScope.isEmpty()?magazine:owners.magazineScope;
    record["reloadTemplates"]=resolvedBehaviorTemplate(record["reloadTemplates"].toObject(),weapon,moving,magazine);
    // Saving relocates replacement files into owned assets. Only an explicit
    // authoring revision changes geometry, not that storage relocation.
    const auto overrides=record.take("modelOverrides").toObject();
    QJsonObject overrideRevisions;
    for(auto it=overrides.begin();it!=overrides.end();++it)
        overrideRevisions[it.key()]=it.value().toObject()["revision"];
    if(!overrideRevisions.isEmpty())record["modelOverrides"]=overrideRevisions;
    for(const auto* key:{"holdingGrips","holdingGripDrafts","annotations","fingerDrafts","gripDraft","mechanismDraft","unfinishedDraft","magazineGripDrafts",
        "weaponName","weaponEditorId","scopeProfiles"})record.remove(key);
    const auto fits=record.take("magazineGrabGrips").toObject();
    const auto defaults=record.take("magazineGrabDefaults").toObject();
    QJsonObject resolvedFits;
    for(const auto* hand:{"left","right"}) {
        const QString suffix=QStringLiteral("|")+hand;
        auto id=fits.value(weapon+'|'+physicalMagazine+suffix);
        if(id.toString().isEmpty())id=defaults.value(weapon+suffix);
        if(id.toString().isEmpty())for(auto it=fits.begin();it!=fits.end();++it) {
            if(it.key().startsWith(weapon+'|')&&it.key().endsWith(suffix)&&!it.value().toString().isEmpty()){id=it.value();break;}
        }
        resolvedFits[hand]=id;
    }
    record["magazineFits"]=resolvedFits;
    for(const auto* field:{"magazineModels","magazineRounds"}) {
        const auto values=record.take(field).toObject();
        QJsonObject selected;
        for(auto it=values.begin();it!=values.end();++it)
            if(it.key().section('|',0,0)==weapon&&it.key().section('|',1,1)==physicalMagazine)selected[it.key()]=it.value();
        record[field]=selected;
    }
    const auto plans=record.take("authoringRoadmaps").toObject();
    QJsonObject resolved;
    for(const auto* field:{"operating","magazineGuide","magazineReference","magazineSetup","collisionDrafts"}) {
        const bool mag=QString::fromLatin1(field)!=QStringLiteral("operating");
        const auto prefix=weapon+(mag?QStringLiteral("/@magazine:"):QStringLiteral("/@moving:"));
        auto value=plans.value(prefix+(mag?magazineScope:moving.join(';'))).toObject().value(field);
        // Match the editor's model-specific operating and magazine-setup
        // reuse before considering its general weapon anchor.
        if(value.isUndefined()&&(!mag||QString::fromLatin1(field)==QStringLiteral("magazineSetup"))) {
            QStringList models=owners.movingModels;
            if(mag) {
                models.clear();const auto operating=resolved.value("operating").toObject();
                const auto anchors=operating["trackAnchors"].toArray();const int track=operating["magazine"].toInt(-1);
                if(track>=0&&track<anchors.size())models.append(anchors[track].toObject()["model"].toString().replace('\\','/').toLower());
            }
            for(auto it=plans.begin();it!=plans.end()&&value.isUndefined();++it) {
                if(!it.key().startsWith(prefix))continue;
                const auto candidate=it.value().toObject()[field].toObject();const auto anchors=candidate["trackAnchors"].toArray();
                const int track=candidate[mag?"emptyTrack":"operating"].toInt(-1);
                if(track<0||track>=anchors.size())continue;
                const auto model=anchors[track].toObject()["model"].toString().replace('\\','/').toLower();
                if(!model.isEmpty()&&models.contains(model))value=candidate;
            }
        }
        if(value.isUndefined()&&(!mag||QString::fromLatin1(field)==QStringLiteral("magazineGuide"))) {
            const auto anchor=plans.value(weapon+"/@anchors").toObject().value(mag?"magazineScope":"movingScope").toString();
            value=plans.value(anchor).toObject().value(field);
            if(value.isUndefined())for(auto it=plans.begin();it!=plans.end();++it)
                if(it.key().startsWith(prefix)&&it.value().toObject().contains(field)){value=it.value().toObject()[field];break;}
        }
        resolved[field]=value;
    }
    // Retain non-part runtime settings on the exact configuration; checklist
    // skips and legacy confirmations are editor state, not runtime behavior.
    for(auto it=plans.begin();it!=plans.end();++it) {
        if(!it.key().startsWith(weapon+'/')||it.key().contains("/@"))continue;
        bool matches=true;
        for(const auto& choice:it.key().section('/',1).split(';')) {
            if(!choice.contains('='))continue;
            const auto part=choice.section('=',1).toLower();
            if(part!=magazine&&!moving.contains(part))matches=false;
        }
        if(matches) {
            auto extra=it.value().toObject();
            for(const auto* field:{"operating","magazineGuide","magazineReference","magazineSetup","collisionDrafts","skips","confirmations"})extra.remove(field);
            if(!extra.isEmpty())resolved[it.key()]=extra;
        }
    }
    record["resolvedPlan"]=resolved;
    return record;
}
// The runtime stores these values once per magazine record, independently
// of receiver profiles. One existing configuration updates that shared record.
inline bool magazineOnlyCaptureChange(QJsonObject before,QJsonObject after) {
    if(before==after)return false;
    for(auto* record:{&before,&after}) {
        record->remove("magazineFits");
        auto plan=record->value("resolvedPlan").toObject();
        plan.remove("magazineGuide");
        (*record)["resolvedPlan"]=plan;
    }
    return before==after;
}

inline bool onlyMagazineEdits(QJsonObject before,QJsonObject after) {
    if(before==after)return false;
    for(auto* record:{&before,&after}) {
        for(const auto* key:{"magazineGrabGrips","magazineGrabDefaults","holdingGrips","holdingGripDrafts","annotations",
             "fingerDrafts","gripDraft","mechanismDraft","unfinishedDraft","magazineGripDrafts","weaponName","weaponEditorId","scopeProfiles"})record->remove(key);
        auto plans=record->value("authoringRoadmaps").toObject();
        for(auto it=plans.begin();it!=plans.end();) {
            auto plan=it.value().toObject();plan.remove("magazineGuide");
            if(it.key().endsWith("/@anchors"))plan.remove("magazineScope");
            if(plan.isEmpty())it=plans.erase(it);else {it.value()=plan;++it;}
        }
        (*record)["authoringRoadmaps"]=plans;
    }
    return before==after;
}

}
