{{- define "aviator.name" -}}
{{- default "aviator" .Values.nameOverride -}}
{{- end -}}

{{- define "aviator.fullname" -}}
{{- printf "%s" (include "aviator.name" .) -}}
{{- end -}}
