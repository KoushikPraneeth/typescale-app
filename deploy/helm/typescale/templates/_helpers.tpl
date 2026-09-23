{{- define "typescale.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "typescale.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "typescale.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "typescale.labels" -}}
helm.sh/chart: {{ include "typescale.chart" . }}
app.kubernetes.io/name: {{ include "typescale.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "typescale.appSelectorLabels" -}}
app.kubernetes.io/name: {{ include "typescale.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: application
{{- end }}

{{- define "typescale.redisSelectorLabels" -}}
app.kubernetes.io/name: {{ include "typescale.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: redis
{{- end }}

{{- define "typescale.redisFullname" -}}
{{- printf "%s-redis" (include "typescale.fullname" . | trunc 57 | trimSuffix "-") }}
{{- end }}

{{- define "typescale.appFullname" -}}
{{- printf "%s-app" (include "typescale.fullname" . | trunc 59 | trimSuffix "-") }}
{{- end }}

{{- define "typescale.publicFullname" -}}
{{- printf "%s-public" (include "typescale.fullname" . | trunc 56 | trimSuffix "-") }}
{{- end }}

{{- define "typescale.defaultDenyFullname" -}}
{{- printf "%s-deny" (include "typescale.fullname" . | trunc 58 | trimSuffix "-") }}
{{- end }}

{{- define "typescale.allowHttpFullname" -}}
{{- printf "%s-allow-http" (include "typescale.fullname" . | trunc 52 | trimSuffix "-") }}
{{- end }}

{{- define "typescale.allowRedisFullname" -}}
{{- printf "%s-allow-redis" (include "typescale.fullname" . | trunc 51 | trimSuffix "-") }}
{{- end }}

{{- define "typescale.allowMetricsFullname" -}}
{{- printf "%s-allow-metrics" (include "typescale.fullname" . | trunc 49 | trimSuffix "-") }}
{{- end }}

{{- define "typescale.testFullname" -}}
{{- printf "%s-test" (include "typescale.fullname" . | trunc 58 | trimSuffix "-") }}
{{- end }}
