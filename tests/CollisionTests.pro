QT = core gui
CONFIG += console c++20 release
CONFIG -= app_bundle debug_and_release
TEMPLATE = app
TARGET = CollisionTests
isEmpty(ROCK_SOURCE_DIR): error(ROCK_SOURCE_DIR is required)
INCLUDEPATH += ../src ../build-fast/native-engine $$ROCK_SOURCE_DIR/src
SOURCES += CollisionTests.cpp ../src/CollisionAuthoring.cpp ../src/areas/collisions/CollisionQuality.cpp ../src/areas/collisions/ConcavityDecomposition.cpp

INCLUDEPATH += ../build-fast/native-engine/lib/qhull/src
SOURCES += $$files($$PWD/../build-fast/native-engine/lib/qhull/src/libqhull_r/*.c)
