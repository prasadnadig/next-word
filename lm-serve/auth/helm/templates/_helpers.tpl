{{- define "lm-serve-auth.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: lm-serve
app.kubernetes.io/component: auth
{{- end -}}
