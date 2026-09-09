{{- define "lm-serve-publisher.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: lm-serve-publisher
app.kubernetes.io/component: lm-serve-publisher
{{- end -}}
