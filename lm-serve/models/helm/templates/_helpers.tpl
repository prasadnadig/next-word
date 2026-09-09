{{- define "lm-serve-models.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: lm-serve
app.kubernetes.io/component: model-runtime
{{- end -}}

{{- define "lm-serve-models.modelK8sName" -}}
{{- $name := lower . -}}
{{- $name = regexReplaceAll "[^a-z0-9-]+" $name "-" -}}
{{- $name = regexReplaceAll "^-+" $name "" -}}
{{- $name = regexReplaceAll "-+$" $name "" -}}
{{- $name -}}
{{- end -}}