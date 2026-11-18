#pragma once
#include <QJsonObject>
#include <QStringList>

namespace reloadmaker {
inline QString behaviorAssignmentKey(const QString& weapon,const QStringList& moving,const QString& magazine={}) {
    return weapon+"/@behavior:"+moving.join(';')+(magazine.isEmpty()?QString():QStringLiteral("|")+magazine);
}
inline QString resolvedBehaviorTemplate(const QJsonObject& assignments,const QString& weapon,const QStringList& moving,const QString& magazine) {
    auto value=assignments[behaviorAssignmentKey(weapon,moving,magazine)].toString();
    if(value.isEmpty())value=assignments[behaviorAssignmentKey(weapon,moving)].toString();
    if(value.isEmpty())value=assignments[weapon].toString();
    return value;
}
}
