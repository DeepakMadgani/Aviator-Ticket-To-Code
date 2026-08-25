#!/bin/bash

APP_VERSION=$1
HELM_VERSION=$2
IMAGE_TAG=$3

if [ -z "$APP_VERSION" -o -z "$HELM_VERSION" -o -z "$IMAGE_TAG" ]
then
  echo "Usage: $0 app_version helm_version image_tag"
  echo "e.g., $0 24.4.1 0.5.1 latest"
  exit 1
fi

update_yaml() {
  FILE_NAME=${1}
  YQ_FILTER=${2}
  NEW_VERSION=${3}
  DOC_INDEX=${4:-}

  # kudos to https://stackoverflow.com/questions/57627243/how-to-prevent-yq-removing-comments-and-empty-lines
  if [ -n "$DOC_INDEX" ]; then
    patch ${FILE_NAME} < <(diff <(yq . ${FILE_NAME}) <(yq "(select(di == ${DOC_INDEX}) | ${YQ_FILTER}) = \"${NEW_VERSION}\"" ${FILE_NAME}))
  else
    patch ${FILE_NAME} < <(diff <(yq . ${FILE_NAME}) <(yq $"${YQ_FILTER}=\"${NEW_VERSION}\"" ${FILE_NAME}))
  fi
  rm -f "${FILE_NAME}.orig"
}

# Update pipeline variables (document index 1 = after --- separator)
update_yaml .gitlab-ci.yml .variables.RELEASE_VERSION ${APP_VERSION} 1

# Update parent chart
update_yaml helm/aviator/Chart.yaml .version ${HELM_VERSION}
update_yaml helm/aviator/Chart.yaml .appVersion ${APP_VERSION}

# Update parent chart image tag
update_yaml helm/aviator/values.yaml .image.tag ${IMAGE_TAG}