find_package(PkgConfig)

PKG_CHECK_MODULES(PC_GR_MAP65 gnuradio-map65)

FIND_PATH(
    GR_MAP65_INCLUDE_DIRS
    NAMES gnuradio/map65/api.h
    HINTS $ENV{MAP65_DIR}/include
        ${PC_MAP65_INCLUDEDIR}
    PATHS ${CMAKE_INSTALL_PREFIX}/include
          /usr/local/include
          /usr/include
)

FIND_LIBRARY(
    GR_MAP65_LIBRARIES
    NAMES gnuradio-map65
    HINTS $ENV{MAP65_DIR}/lib
        ${PC_MAP65_LIBDIR}
    PATHS ${CMAKE_INSTALL_PREFIX}/lib
          ${CMAKE_INSTALL_PREFIX}/lib64
          /usr/local/lib
          /usr/local/lib64
          /usr/lib
          /usr/lib64
          )

include("${CMAKE_CURRENT_LIST_DIR}/gnuradio-map65Target.cmake")

INCLUDE(FindPackageHandleStandardArgs)
FIND_PACKAGE_HANDLE_STANDARD_ARGS(GR_MAP65 DEFAULT_MSG GR_MAP65_LIBRARIES GR_MAP65_INCLUDE_DIRS)
MARK_AS_ADVANCED(GR_MAP65_LIBRARIES GR_MAP65_INCLUDE_DIRS)
