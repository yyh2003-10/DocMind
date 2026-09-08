# -*- coding: utf-8 -*-
"""Replace all emojis in SettingsView.xaml with vector Path icons or pure text."""
import re, sys

FILE = r'e:\DocMindY\DocMind\Views\SettingsView.xaml'

with open(FILE, 'r', encoding='utf-8') as f:
    content = f.read()

# Define all replacements as (old, new) pairs
# Each pair is a unique string fragment to find and replace

replacements = []

# === Mode 3: Standalone emoji TextBlock (Text is ONLY an emoji) ===

# Line: nav bar icons
replacements.append((
    '<TextBlock Text="\U0001f3a8" FontSize="16" Margin="0,0,10,0" VerticalAlignment="Center"/>',
    '<Path Data="{StaticResource IconPalette}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
replacements.append((
    '<TextBlock Text="\U0001f50c" FontSize="16" Margin="0,0,10,0" VerticalAlignment="Center"/>',
    '<Path Data="{StaticResource IconPlug}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
# Note: the globe nav item might already be replaced by previous SearchReplace

# SectionIcon standalone emojis
replacements.append((
    '<TextBlock Text="\U0001f511" Style="{StaticResource SectionIcon}"/>',
    '<Path Data="{StaticResource IconLock}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
replacements.append((
    '<TextBlock Text="\U0001f9e0" Style="{StaticResource SectionIcon}"/>',
    '<Path Data="{StaticResource IconBrain}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
replacements.append((
    '<TextBlock Text="\U0001f39a" Style="{StaticResource SectionIcon}"/>',
    '<Path Data="{StaticResource IconSliders}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
replacements.append((
    '<TextBlock Text="\u2702\ufe0f" Style="{StaticResource SectionIcon}"/>',
    '<Path Data="{StaticResource IconScissors}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
replacements.append((
    '<TextBlock Text="\U0001f441\ufe0f" Style="{StaticResource SectionIcon}"/>',
    '<Path Data="{StaticResource IconEye}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
replacements.append((
    '<TextBlock Text="\U0001f5bc\ufe0f" Style="{StaticResource SectionIcon}"/>',
    '<Path Data="{StaticResource IconImage}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
replacements.append((
    '<TextBlock Text="\U0001f50c" Style="{StaticResource SectionIcon}"/>',
    '<Path Data="{StaticResource IconPlug}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
replacements.append((
    '<TextBlock Text="\U0001f5c2" Style="{StaticResource SectionIcon}"/>',
    '<Path Data="{StaticResource IconFolder}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
replacements.append((
    '<TextBlock Text="\U0001f9e9" Style="{StaticResource SectionIcon}"/>',
    '<Path Data="{StaticResource IconPuzzle}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
replacements.append((
    '<TextBlock Text="\U0001fa7a" Style="{StaticResource SectionIcon}"/>',
    '<Path Data="{StaticResource IconStethoscope}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))
# Globe SectionIcon (may already be replaced)
replacements.append((
    '<TextBlock Text="\U0001f310" Style="{StaticResource SectionIcon}"/>',
    '<Path Data="{StaticResource IconGlobe}" Style="{StaticResource IconSmStyle}" Margin="0,0,10,0" VerticalAlignment="Center"/>'
))

# Theme card emojis (FontSize=26)
replacements.append((
    '<TextBlock Text="\u2600\ufe0f" FontSize="26" HorizontalAlignment="Center" Margin="0,0,0,6"/>',
    '<Path Data="{StaticResource IconSun}" Style="{StaticResource IconSmStyle}" Width="26" Height="26" HorizontalAlignment="Center" Margin="0,0,0,6"/>'
))
replacements.append((
    '<TextBlock Text="\U0001f319" FontSize="26" HorizontalAlignment="Center" Margin="0,0,0,6"/>',
    '<Path Data="{StaticResource IconMoon}" Style="{StaticResource IconSmStyle}" Width="26" Height="26" HorizontalAlignment="Center" Margin="0,0,0,6"/>'
))

# === Mode 2: TextBlock with emoji prefix ===

replacements.append((
    '<TextBlock Text="\U0001f916 AI \u6a21\u578b\u4e0e\u5bf9\u8bdd\u914d\u7f6e" FontSize="18" FontWeight="Bold" Foreground="{StaticResource TextPrimaryBrush}"/>',
    '<StackPanel Orientation="Horizontal">\n                                <Path Data="{StaticResource IconAi}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                <TextBlock Text="AI \u6a21\u578b\u4e0e\u5bf9\u8bdd\u914d\u7f6e" FontSize="18" FontWeight="Bold" Foreground="{StaticResource TextPrimaryBrush}"/>\n                            </StackPanel>'
))

replacements.append((
    '<TextBlock Text="\u2705 \u6a21\u578b/Key \u53c2\u6570\u4fdd\u5b58\u540e\u7acb\u5373\u751f\u6548" FontSize="11" Foreground="{StaticResource SuccessBrush}"/>',
    '<StackPanel Orientation="Horizontal">\n                                <Path Data="{StaticResource IconCheck}" Style="{StaticResource IconSmStyle}" Stroke="{StaticResource SuccessBrush}" Margin="0,0,6,0"/>\n                                <TextBlock Text="\u6a21\u578b/Key \u53c2\u6570\u4fdd\u5b58\u540e\u7acb\u5373\u751f\u6548" FontSize="11" Foreground="{StaticResource SuccessBrush}"/>\n                            </StackPanel>'
))

replacements.append((
    '<TextBlock Text="\U0001f31f \u672c\u5730 AI \u73af\u5883\u667a\u80fd\u611f\u77e5" FontWeight="SemiBold" FontSize="13"/>',
    '<Path Data="{StaticResource IconStar}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                        <TextBlock Text="\u672c\u5730 AI \u73af\u5883\u667a\u80fd\u611f\u77e5" FontWeight="SemiBold" FontSize="13"/>'
))

replacements.append((
    '<TextBlock Text="\U0001f4ca \u5f53\u524d\u751f\u6548\u914d\u7f6e\uff08\u6765\u81ea\u540e\u7aef\uff09" FontWeight="SemiBold" FontSize="13" Margin="0,0,0,10"/>',
    '<StackPanel Orientation="Horizontal" Margin="0,0,0,10">\n                                <Path Data="{StaticResource IconChart}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                <TextBlock Text="\u5f53\u524d\u751f\u6548\u914d\u7f6e\uff08\u6765\u81ea\u540e\u7aef\uff09" FontWeight="SemiBold" FontSize="13"/>\n                            </StackPanel>'
))

replacements.append((
    '<TextBlock Text="\U0001f4a1 \u5173\u952e\u8fd0\u884c\u53c2\u6570\u5df2\u66f4\u65b0" FontWeight="Bold" Foreground="{DynamicResource PrimaryBrush}"/>',
    '<StackPanel Orientation="Horizontal">\n                                <Path Data="{StaticResource IconLightbulb}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                <TextBlock Text="\u5173\u952e\u8fd0\u884c\u53c2\u6570\u5df2\u66f4\u65b0" FontWeight="Bold" Foreground="{DynamicResource PrimaryBrush}"/>\n                            </StackPanel>'
))

replacements.append((
    '<TextBlock Text="\u23f3 \u6b63\u5728\u626b\u63cf\u672c\u5730 Ollama / LM Studio \u53ca\u6a21\u578b\u8d44\u4ea7..."',
    '<TextBlock Text="\u6b63\u5728\u626b\u63cf\u672c\u5730 Ollama / LM Studio \u53ca\u6a21\u578b\u8d44\u4ea7..."'
))

replacements.append((
    '<TextBlock Text="\U0001f4a1 \u672a\u68c0\u6d4b\u5230\u8fd0\u884c\u4e2d\u7684 LM Studio \u6216 Ollama \u670d\u52a1\u3002',
    '<TextBlock Text="\u672a\u68c0\u6d4b\u5230\u8fd0\u884c\u4e2d\u7684 LM Studio \u6216 Ollama \u670d\u52a1\u3002'
))

# Panel 2 title
replacements.append((
    '<TextBlock Text="\U0001f4da \u77e5\u8bc6\u5e93\u4e0e\u68c0\u7d22\u8c03\u4f18" FontSize="18" FontWeight="Bold" Foreground="{StaticResource TextPrimaryBrush}"/>',
    '<StackPanel Orientation="Horizontal">\n                                <Path Data="{StaticResource IconBook}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                <TextBlock Text="\u77e5\u8bc6\u5e93\u4e0e\u68c0\u7d22\u8c03\u4f18" FontSize="18" FontWeight="Bold" Foreground="{StaticResource TextPrimaryBrush}"/>\n                            </StackPanel>'
))

replacements.append((
    '<TextBlock Text="\U0001f504 \u4fdd\u5b58\u540e\u9700\u91cd\u542f\u540e\u7aef\u751f\u6548" FontSize="11" Foreground="{StaticResource TextSecondaryBrush}"/>',
    '<StackPanel Orientation="Horizontal">\n                                <Path Data="{StaticResource IconRefresh}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                <TextBlock Text="\u4fdd\u5b58\u540e\u9700\u91cd\u542f\u540e\u7aef\u751f\u6548" FontSize="11" Foreground="{StaticResource TextSecondaryBrush}"/>\n                            </StackPanel>'
))

# ComboBox items
replacements.append((
    '<TextBlock Text="\U0001f310 \u77e5\u8bc6\u5e93\u4f18\u5148 + \u901a\u7528\u77e5\u8bc6\u589e\u5f3a\uff08\u63a8\u8350\uff09" FontWeight="SemiBold"/>',
    '<StackPanel Orientation="Horizontal">\n                                                <Path Data="{StaticResource IconGlobe}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                                <TextBlock Text="\u77e5\u8bc6\u5e93\u4f18\u5148 + \u901a\u7528\u77e5\u8bc6\u589e\u5f3a\uff08\u63a8\u8350\uff09" FontWeight="SemiBold"/>\n                                            </StackPanel>'
))

replacements.append((
    '<TextBlock Text="\U0001f3af \u4e25\u683c\u77e5\u8bc6\u5e93\u6a21\u5f0f" FontWeight="SemiBold"/>',
    '<StackPanel Orientation="Horizontal">\n                                                <Path Data="{StaticResource IconTarget}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                                <TextBlock Text="\u4e25\u683c\u77e5\u8bc6\u5e93\u6a21\u5f0f" FontWeight="SemiBold"/>\n                                            </StackPanel>'
))

# Panel 3 title
replacements.append((
    '<TextBlock Text="\U0001f3a8 \u754c\u9762\u5916\u89c2\u4e0e\u4e2a\u6027\u5316" FontSize="18" FontWeight="Bold" Foreground="{StaticResource TextPrimaryBrush}"/>',
    '<StackPanel Orientation="Horizontal">\n                                <Path Data="{StaticResource IconPalette}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                <TextBlock Text="\u754c\u9762\u5916\u89c2\u4e0e\u4e2a\u6027\u5316" FontSize="18" FontWeight="Bold" Foreground="{StaticResource TextPrimaryBrush}"/>\n                            </StackPanel>'
))

# Panel 4 title
replacements.append((
    '<TextBlock Text="\U0001f50c \u786c\u4ef6\u7b97\u529b\u4e0e\u7cfb\u7edf\u4f53\u68c0" FontSize="18" FontWeight="Bold" Foreground="{StaticResource TextPrimaryBrush}"/>',
    '<StackPanel Orientation="Horizontal">\n                                <Path Data="{StaticResource IconPlug}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                <TextBlock Text="\u786c\u4ef6\u7b97\u529b\u4e0e\u7cfb\u7edf\u4f53\u68c0" FontSize="18" FontWeight="Bold" Foreground="{StaticResource TextPrimaryBrush}"/>\n                            </StackPanel>'
))

# GPU available check
replacements.append((
    '<TextBlock Text="  \u2713"\n                                                   Visibility="{Binding GpuWarning.GpuAvailable, Converter={StaticResource BoolToVis}}"\n                                                   Foreground="{StaticResource SuccessBrush}"\n                                                   FontSize="14" FontWeight="SemiBold" VerticalAlignment="Center"/>',
    '<Path Data="{StaticResource IconCheck}" Style="{StaticResource IconSmStyle}" Stroke="{StaticResource SuccessBrush}"\n                                                   Visibility="{Binding GpuWarning.GpuAvailable, Converter={StaticResource BoolToVis}}"\n                                                   Margin="6,0,0,0" VerticalAlignment="Center"/>'
))

replacements.append((
    '<TextBlock Text="\u23f3 \u6b63\u5728\u68c0\u6d4b GPU \u8fd0\u884c\u73af\u5883..."',
    '<TextBlock Text="\u6b63\u5728\u68c0\u6d4b GPU \u8fd0\u884c\u73af\u5883..."'
))

replacements.append((
    '<TextBlock Text="\u23f3 \u6b63\u5728\u68c0\u6d4b\u8fd0\u884c\u4f9d\u8d56\u5c31\u7eea\u72b6\u6001..."',
    '<TextBlock Text="\u6b63\u5728\u68c0\u6d4b\u8fd0\u884c\u4f9d\u8d56\u5c31\u7eea\u72b6\u6001..."'
))

replacements.append((
    '<TextBlock Text="\U0001f4a1 \u8be6\u7ec6\u4ecb\u7ecd\u5404\u4f9d\u8d56\u7684\u5b89\u88c5\u4e0e\u72b6\u6001\u542b\u4e49\uff0c\u53ef\u5728\u4e0a\u65b9\u300cGPU \u786c\u4ef6\u52a0\u901f\u300d\u5361\u7247\u6216\u4e0b\u65b9\u300c\u7cfb\u7edf\u73af\u5883\u81ea\u68c0\u300d\u4e2d\u67e5\u770b\u3002"',
    '<TextBlock Text="\u8be6\u7ec6\u4ecb\u7ecd\u5404\u4f9d\u8d56\u7684\u5b89\u88c5\u4e0e\u72b6\u6001\u542b\u4e49\uff0c\u53ef\u5728\u4e0a\u65b9\u300cGPU \u786c\u4ef6\u52a0\u901f\u300d\u5361\u7247\u6216\u4e0b\u65b9\u300c\u7cfb\u7edf\u73af\u5883\u81ea\u68c0\u300d\u4e2d\u67e5\u770b\u3002"'
))

replacements.append((
    '<TextBlock Text="\u23f3 \u6b63\u5728\u68c0\u6d4b\u5168\u7cfb\u7edf\u5404\u9879\u73af\u5883\u4e0e\u7f51\u7edc\u8fde\u901a\u6027\uff0c\u8bf7\u7a0d\u5019..."',
    '<TextBlock Text="\u6b63\u5728\u68c0\u6d4b\u5168\u7cfb\u7edf\u5404\u9879\u73af\u5883\u4e0e\u7f51\u7edc\u8fde\u901a\u6027\uff0c\u8bf7\u7a0d\u5019..."'
))

# Panel 5 title
replacements.append((
    '<TextBlock Text="\U0001f310 \u540e\u7aef\u670d\u52a1\u4e0e\u5173\u4e8e\u4fe1\u606f" FontSize="18" FontWeight="Bold" Foreground="{StaticResource TextPrimaryBrush}"/>',
    '<StackPanel Orientation="Horizontal">\n                                <Path Data="{StaticResource IconGlobe}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                <TextBlock Text="\u540e\u7aef\u670d\u52a1\u4e0e\u5173\u4e8e\u4fe1\u606f" FontSize="18" FontWeight="Bold" Foreground="{StaticResource TextPrimaryBrush}"/>\n                            </StackPanel>'
))

# === Mode 4: Setter with emoji ===
replacements.append((
    '<Setter Property="Text" Value="\u5df2\u914d\u7f6e \u2713"/>',
    '<Setter Property="Text" Value="\u5df2\u914d\u7f6e"/>'
))
replacements.append((
    '<Setter Property="Text" Value="\u25cf \u5df2\u914d\u7f6e \u2713"/>',
    '<Setter Property="Text" Value="\u25cf \u5df2\u914d\u7f6e"/>'
))
replacements.append((
    '<Setter Property="Content" Value="\u23fb \u505c\u7528"/>',
    '<Setter Property="Content" Value="\u505c\u7528"/>'
))
replacements.append((
    '<Setter Property="Content" Value="\u23fb \u542f\u7528"/>',
    '<Setter Property="Content" Value="\u542f\u7528"/>'
))

# === Mode 5: Pure symbol TextBlocks ===
replacements.append((
    '<TextBlock Text="\u2713 " FontSize="12" FontWeight="Bold"\n                                                               Foreground="{StaticResource SuccessBrush}" Margin="0,0,6,0"\n                                                               Visibility="{Binding Ok, Converter={StaticResource BoolToVis}}"/>',
    '<Path Data="{StaticResource IconCheck}" Style="{StaticResource IconSmStyle}" Stroke="{StaticResource SuccessBrush}"\n                                                               Margin="0,0,6,0" VerticalAlignment="Center"\n                                                               Visibility="{Binding Ok, Converter={StaticResource BoolToVis}}"/>'
))
replacements.append((
    '<TextBlock Text="\u2717 " FontSize="12" FontWeight="Bold"\n                                                               Foreground="{StaticResource WarningBrush}" Margin="0,0,6,0"\n                                                               Visibility="{Binding Ok, Converter={StaticResource InvertBoolVis}}"/>',
    '<Path Data="{StaticResource IconClose}" Style="{StaticResource IconSmStyle}" Stroke="{StaticResource WarningBrush}"\n                                                               Margin="0,0,6,0" VerticalAlignment="Center"\n                                                               Visibility="{Binding Ok, Converter={StaticResource InvertBoolVis}}"/>'
))

# === DataTrigger ✓/✗ complex ===
old_dt = '''                                                <TextBlock FontSize="11" Margin="0,0,6,0">
                                                    <TextBlock.Style>
                                                        <Style TargetType="TextBlock">
                                                            <Style.Triggers>
                                                                <DataTrigger Binding="{Binding Installed}" Value="True">
                                                                    <Setter Property="Text" Value="\u2713"/>
                                                                    <Setter Property="Foreground" Value="{StaticResource SuccessBrush}"/>
                                                                </DataTrigger>
                                                                <DataTrigger Binding="{Binding Installed}" Value="False">
                                                                    <Setter Property="Text" Value="\u2717"/>
                                                                    <Setter Property="Foreground" Value="{StaticResource WarningBrush}"/>
                                                                </DataTrigger>
                                                            </Style.Triggers>
                                                        </Style>
                                                    </TextBlock.Style>
                                                </TextBlock>'''
new_dt = '''                                                <Path Data="{StaticResource IconCheck}" Style="{StaticResource IconSmStyle}" Stroke="{StaticResource SuccessBrush}" Margin="0,0,6,0" VerticalAlignment="Center"
                                                      Visibility="{Binding Installed, Converter={StaticResource BoolToVis}}"/>
                                                <Path Data="{StaticResource IconClose}" Style="{StaticResource IconSmStyle}" Stroke="{StaticResource WarningBrush}" Margin="0,0,6,0" VerticalAlignment="Center"
                                                      Visibility="{Binding Installed, Converter={StaticResource InvertBoolVis}}"/>'''
replacements.append((old_dt, new_dt))

# === Mode 1: Button Content="emoji text" (self-closing) ===
# Each needs to remove Content and add child StackPanel

# Button: Refresh restart
replacements.append((
    '<Button DockPanel.Dock="Right"\n                                    Content="\U0001f504 \u7acb\u5373\u5e73\u6ed1\u91cd\u542f\u540e\u7aef"\n                                    Command="{Binding RestartBackendCommand}"\n                                    Style="{StaticResource PrimaryButtonStyle}"\n                                    Padding="14,6"\n                                    Margin="12,0,0,0"\n                                    VerticalAlignment="Center"/>',
    '<Button DockPanel.Dock="Right"\n                                    Command="{Binding RestartBackendCommand}"\n                                    Style="{StaticResource PrimaryButtonStyle}"\n                                    Padding="14,6"\n                                    Margin="12,0,0,0"\n                                    VerticalAlignment="Center">\n                                <StackPanel Orientation="Horizontal">\n                                    <Path Data="{StaticResource IconRefresh}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                    <TextBlock Text="\u7acb\u5373\u5e73\u6ed1\u91cd\u542f\u540e\u7aef"/>\n                                </StackPanel>\n                            </Button>'
))

# Button: Refresh re-detect (DockPanel)
replacements.append((
    'Content="\U0001f504 \u91cd\u65b0\u63a2\u6d4b"\n                                            Command="{Binding DetectLocalAiCommand}"',
    'Command="{Binding DetectLocalAiCommand}"'
))
# Need to also close the button and add content - this is tricky for multi-line
# Let me handle these button cases differently

# Actually, let me handle the multi-line button cases with specific patterns
# For the "重新探测" button
replacements.append((
    '<Button DockPanel.Dock="Right" Content="\U0001f504 \u91cd\u65b0\u63a2\u6d4b"\n                                            Command="{Binding DetectLocalAiCommand}"\n                                            IsEnabled="{Binding IsDetectingLocalAi, Converter={StaticResource BoolInvertConverter}}"\n                                            Style="{StaticResource TextButtonStyle}" FontSize="11" Padding="6,2"/>',
    '<Button DockPanel.Dock="Right"\n                                            Command="{Binding DetectLocalAiCommand}"\n                                            IsEnabled="{Binding IsDetectingLocalAi, Converter={StaticResource BoolInvertConverter}}"\n                                            Style="{StaticResource TextButtonStyle}" FontSize="11" Padding="6,2">\n                                        <StackPanel Orientation="Horizontal">\n                                            <Path Data="{StaticResource IconRefresh}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                            <TextBlock Text="\u91cd\u65b0\u63a2\u6d4b"/>\n                                        </StackPanel>\n                                    </Button>'
))

# Button: Bolt LM Studio
replacements.append((
    '<Button Content="\u26a1 \u4e00\u952e\u5957\u7528 LM Studio \u6781\u901f\u672c\u5730\u65b9\u6848"\n                                                Command="{Binding ApplyLmStudioPresetCommand}"\n                                                IsEnabled="{Binding IsLmStudioRunning}"\n                                                Style="{StaticResource PrimaryButtonStyle}"\n                                                Padding="12,6" Margin="0,0,8,4" FontSize="11"\n                                                ToolTip="\u81ea\u52a8\u7ed1\u5b9a LM Studio \u5730\u5740\u4e0e\u6a21\u578b\uff0c\u663e\u5361\u5168\u901f\u63a8\u7406\uff0c\u5411\u91cf\u8d70\u8f7b\u91cf CPU \u5f15\u64ce"/>',
    '<Button Command="{Binding ApplyLmStudioPresetCommand}"\n                                                IsEnabled="{Binding IsLmStudioRunning}"\n                                                Style="{StaticResource PrimaryButtonStyle}"\n                                                Padding="12,6" Margin="0,0,8,4" FontSize="11"\n                                                ToolTip="\u81ea\u52a8\u7ed1\u5b9a LM Studio \u5730\u5740\u4e0e\u6a21\u578b\uff0c\u663e\u5361\u5168\u901f\u63a8\u7406\uff0c\u5411\u91cf\u8d70\u8f7b\u91cf CPU \u5f15\u64ce">\n                                            <StackPanel Orientation="Horizontal">\n                                                <Path Data="{StaticResource IconBolt}" Style="{StaticResource IconSmStyle}" Stroke="{StaticResource PrimaryBrush}" Margin="0,0,6,0"/>\n                                                <TextBlock Text="\u4e00\u952e\u5957\u7528 LM Studio \u6781\u901f\u672c\u5730\u65b9\u6848"/>\n                                            </StackPanel>\n                                        </Button>'
))

# Button: Check Ollama
replacements.append((
    '<Button Content="\U0001f7e2 \u4e00\u952e\u5957\u7528 Ollama \u4e00\u4f53\u5316\u672c\u5730\u65b9\u6848"\n                                                Command="{Binding ApplyOllamaPresetCommand}"\n                                                IsEnabled="{Binding IsOllamaRunning}"\n                                                Style="{StaticResource SecondaryButtonStyle}"\n                                                Padding="12,6" Margin="0,0,8,4" FontSize="11"\n                                                ToolTip="\u81ea\u52a8\u7ed1\u5b9a Ollama \u670d\u52a1\u4e0e\u5df2\u4e0b\u8f7d\u6a21\u578b\uff0c\u5185\u7f6e CUDA \u5f15\u64ce\u52a0\u901f"/>',
    '<Button Command="{Binding ApplyOllamaPresetCommand}"\n                                                IsEnabled="{Binding IsOllamaRunning}"\n                                                Style="{StaticResource SecondaryButtonStyle}"\n                                                Padding="12,6" Margin="0,0,8,4" FontSize="11"\n                                                ToolTip="\u81ea\u52a8\u7ed1\u5b9a Ollama \u670d\u52a1\u4e0e\u5df2\u4e0b\u8f7d\u6a21\u578b\uff0c\u5185\u7f6e CUDA \u5f15\u64ce\u52a0\u901f">\n                                            <StackPanel Orientation="Horizontal">\n                                                <Path Data="{StaticResource IconCheck}" Style="{StaticResource IconSmStyle}" Stroke="{StaticResource SuccessBrush}" Margin="0,0,6,0"/>\n                                                <TextBlock Text="\u4e00\u952e\u5957\u7528 Ollama \u4e00\u4f53\u5316\u672c\u5730\u65b9\u6848"/>\n                                            </StackPanel>\n                                        </Button>'
))

# Button: Star set default
replacements.append((
    '<Button Content="\u2b50 \u8bbe\u4e3a\u9ed8\u8ba4"\n                                                        Command="{Binding SetDefaultProviderCommand}"\n                                                        IsEnabled="{Binding HasSelectedProfile}"\n                                                        Style="{StaticResource SecondaryButtonStyle}"\n                                                        Padding="10,4" Margin="0,0,6,4" FontSize="11"\n                                                        ToolTip="\u628a\u9009\u4e2d\u7684\u81ea\u5b9a\u4e49\u670d\u52a1\u5546\u6807\u8bb0\u4e3a\u9ed8\u8ba4\uff08\u4ec5\u8bbe\u7f6e\u9875\u9ad8\u4eae\uff1b\u5bf9\u8bdd\u9875\u300c\u9ed8\u8ba4\u300d\u9879\u7528\u8bbe\u7f6e\u9875\u5f53\u524d\u6a21\u578b\u914d\u7f6e\uff0c\u5982\u9700\u5bf9\u8bdd\u9875\u9ed8\u8ba4\u4f7f\u7528\u8be5\u670d\u52a1\u5546\u8bf7\u70b9\u300c\u5e94\u7528\u8be5\u670d\u52a1\u5546\u300d\uff09"/>',
    '<Button Command="{Binding SetDefaultProviderCommand}"\n                                                        IsEnabled="{Binding HasSelectedProfile}"\n                                                        Style="{StaticResource SecondaryButtonStyle}"\n                                                        Padding="10,4" Margin="0,0,6,4" FontSize="11"\n                                                        ToolTip="\u628a\u9009\u4e2d\u7684\u81ea\u5b9a\u4e49\u670d\u52a1\u5546\u6807\u8bb0\u4e3a\u9ed8\u8ba4\uff08\u4ec5\u8bbe\u7f6e\u9875\u9ad8\u4eae\uff1b\u5bf9\u8bdd\u9875\u300c\u9ed8\u8ba4\u300d\u9879\u7528\u8bbe\u7f6e\u9875\u5f53\u524d\u6a21\u578b\u914d\u7f6e\uff0c\u5982\u9700\u5bf9\u8bdd\u9875\u9ed8\u8ba4\u4f7f\u7528\u8be5\u670d\u52a1\u5546\u8bf7\u70b9\u300c\u5e94\u7528\u8be5\u670d\u52a1\u5546\u300d\uff09">\n                                                    <StackPanel Orientation="Horizontal">\n                                                        <Path Data="{StaticResource IconStar}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                                        <TextBlock Text="\u8bbe\u4e3a\u9ed8\u8ba4"/>\n                                                    </StackPanel>\n                                                </Button>'
))

# Button: Trash delete
replacements.append((
    '<Button Content="\U0001f5d1 \u5220\u9664"\n                                                        Command="{Binding DeleteProfileCommand}"\n                                                        IsEnabled="{Binding HasSelectedProfile}"\n                                                        Style="{StaticResource TextButtonStyle}"\n                                                        Padding="8,4" Margin="0,0,0,4" FontSize="11"\n                                                        ToolTip="\u5220\u9664\u9009\u4e2d\u7684\u81ea\u5b9a\u4e49\u670d\u52a1\u5546\uff08\u786e\u8ba4\u540e\u4e0d\u53ef\u6062\u590d\uff09"/>',
    '<Button Command="{Binding DeleteProfileCommand}"\n                                                        IsEnabled="{Binding HasSelectedProfile}"\n                                                        Style="{StaticResource TextButtonStyle}"\n                                                        Padding="8,4" Margin="0,0,0,4" FontSize="11"\n                                                        ToolTip="\u5220\u9664\u9009\u4e2d\u7684\u81ea\u5b9a\u4e49\u670d\u52a1\u5546\uff08\u786e\u8ba4\u540e\u4e0d\u53ef\u6062\u590d\uff09">\n                                                    <StackPanel Orientation="Horizontal">\n                                                        <Path Data="{StaticResource IconTrash}" Style="{StaticResource IconSmStyle}" Stroke="{StaticResource DangerBrush}" Margin="0,0,6,0"/>\n                                                        <TextBlock Text="\u5220\u9664"/>\n                                                    </StackPanel>\n                                                </Button>'
))

# Button: Link to API key
replacements.append((
    'Content="\U0001f517 \u524d\u5f80\u7533\u8bf7/\u7ba1\u7406\u6b64\u670d\u52a1\u5546 API Key \u2197"\n                                                    Command="{Binding OpenPresetConsoleUrlCommand}"',
    'Command="{Binding OpenPresetConsoleUrlCommand}"'
))
# Also need to close this button and add content
replacements.append((
    'ToolTip="\u5728\u9ed8\u8ba4\u6d4f\u89c8\u5668\u4e2d\u6253\u5f00\u8be5\u670d\u52a1\u5546\u7684 API \u5bc6\u94a5\u7ba1\u7406\u63a7\u5236\u53f0"/>',
    'ToolTip="\u5728\u9ed8\u8ba4\u6d4f\u89c8\u5668\u4e2d\u6253\u5f00\u8be5\u670d\u52a1\u5546\u7684 API \u5bc6\u94a5\u7ba1\u7406\u63a7\u5236\u53f0">\n                                                <StackPanel Orientation="Horizontal">\n                                                    <Path Data="{StaticResource IconLink}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                                    <TextBlock Text="\u524d\u5f80\u7533\u8bf7/\u7ba1\u7406\u6b64\u670d\u52a1\u5546 API Key"/>\n                                                </StackPanel>\n                                            </Button>'
))

# Button: Close (✕) remove model
replacements.append((
    '<Button DockPanel.Dock="Right"\n                                                                Content="\u2715"\n                                                                Command="{Binding DataContext.RemoveModelFromProviderCommand, RelativeSource={RelativeSource AncestorType=ItemsControl}}"\n                                                                CommandParameter="{Binding}"\n                                                                Style="{StaticResource TextButtonStyle}"\n                                                                Padding="6,2" FontSize="11"\n                                                                ToolTip="\u4ece\u8be5\u670d\u52a1\u5546\u6a21\u578b\u5217\u8868\u4e2d\u79fb\u9664"/>',
    '<Button DockPanel.Dock="Right"\n                                                                Command="{Binding DataContext.RemoveModelFromProviderCommand, RelativeSource={RelativeSource AncestorType=ItemsControl}}"\n                                                                CommandParameter="{Binding}"\n                                                                Style="{StaticResource TextButtonStyle}"\n                                                                Padding="6,2" FontSize="11"\n                                                                ToolTip="\u4ece\u8be5\u670d\u52a1\u5546\u6a21\u578b\u5217\u8868\u4e2d\u79fb\u9664">\n                                                            <Path Data="{StaticResource IconClose}" Style="{StaticResource IconSmStyle}"/>\n                                                        </Button>'
))

# Button: Plus add provider
replacements.append((
    '<Button Content="\u2795 \u6dfb\u52a0\u4e3a\u670d\u52a1\u5546"\n                                            Command="{Binding SaveProfileCommand}"\n                                            Style="{StaticResource PrimaryButtonStyle}"\n                                            Padding="12,5" Margin="0,0,8,4" FontSize="11"\n                                            ToolTip="\u628a\u53f3\u4fa7\u586b\u5199\u7684\u540d\u79f0/\u63d0\u4f9b\u5546/\u5730\u5740/Key/\u6a21\u578b\u5217\u8868\u5b58\u4e3a\u4e00\u4e2a\u81ea\u5b9a\u4e49\u670d\u52a1\u5546\uff08Key \u52a0\u5bc6\u4fdd\u5b58\uff09\uff0c\u6dfb\u52a0\u540e\u5373\u53ef\u5728\u5de6\u4fa7\u5217\u8868\u4e0e\u5bf9\u8bdd\u9875\u9009\u7528"/>',
    '<Button Command="{Binding SaveProfileCommand}"\n                                            Style="{StaticResource PrimaryButtonStyle}"\n                                            Padding="12,5" Margin="0,0,8,4" FontSize="11"\n                                            ToolTip="\u628a\u53f3\u4fa7\u586b\u5199\u7684\u540d\u79f0/\u63d0\u4f9b\u5546/\u5730\u5740/Key/\u6a21\u578b\u5217\u8868\u5b58\u4e3a\u4e00\u4e2a\u81ea\u5b9a\u4e49\u670d\u52a1\u5546\uff08Key \u52a0\u5bc6\u4fdd\u5b58\uff09\uff0c\u6dfb\u52a0\u540e\u5373\u53ef\u5728\u5de6\u4fa7\u5217\u8868\u4e0e\u5bf9\u8bdd\u9875\u9009\u7528">\n                                        <StackPanel Orientation="Horizontal">\n                                            <Path Data="{StaticResource IconPlus}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                            <TextBlock Text="\u6dfb\u52a0\u4e3a\u670d\u52a1\u5546"/>\n                                        </StackPanel>\n                                    </Button>'
))

# Button: Play apply
replacements.append((
    '<Button Content="\u25b6 \u5e94\u7528\u8be5\u670d\u52a1\u5546"\n                                            Command="{Binding ApplyProfileCommand}"\n                                            IsEnabled="{Binding IsApplyingProfile, Converter={StaticResource BoolInvertConverter}}"\n                                            Style="{StaticResource SecondaryButtonStyle}"\n                                            Padding="12,5" Margin="0,0,8,4" FontSize="11"\n                                            ToolTip="\u5e94\u7528\u53f3\u4fa7\u5f53\u524d\u914d\u7f6e\u5230\u540e\u7aef\uff08\u4fdd\u5b58 + \u63a8\u9001\uff0c\u514d\u91cd\u542f\u751f\u6548\uff09"/>',
    '<Button Command="{Binding ApplyProfileCommand}"\n                                            IsEnabled="{Binding IsApplyingProfile, Converter={StaticResource BoolInvertConverter}}"\n                                            Style="{StaticResource SecondaryButtonStyle}"\n                                            Padding="12,5" Margin="0,0,8,4" FontSize="11"\n                                            ToolTip="\u5e94\u7528\u53f3\u4fa7\u5f53\u524d\u914d\u7f6e\u5230\u540e\u7aef\uff08\u4fdd\u5b58 + \u63a8\u9001\uff0c\u514d\u91cd\u542f\u751f\u6548\uff09">\n                                        <TextBlock Text="\u5e94\u7528\u8be5\u670d\u52a1\u5546"/>\n                                    </Button>'
))

# Button: Search check all
replacements.append((
    '<Button Content="\U0001f50d \u4e00\u952e\u4f53\u68c0\u5168\u90e8\u670d\u52a1\u5546"\n                                            Command="{Binding CheckAllProfilesCommand}"\n                                            IsEnabled="{Binding IsCheckingProfiles, Converter={StaticResource BoolInvertConverter}}"\n                                            Style="{StaticResource SecondaryButtonStyle}"\n                                            Padding="12,5" Margin="0,0,0,4" FontSize="11"\n                                            ToolTip="\u5bf9\u5168\u90e8\u5df2\u5b58\u670d\u52a1\u5546\u9010\u4e2a\u6d4b\u8bd5\u8fde\u901a\u6027\uff08\u534f\u8bae/\u5730\u5740/Key/\u6a21\u578b\uff09\uff0c\u7ed3\u679c\u5217\u5728\u4e0b\u65b9"/>',
    '<Button Command="{Binding CheckAllProfilesCommand}"\n                                            IsEnabled="{Binding IsCheckingProfiles, Converter={StaticResource BoolInvertConverter}}"\n                                            Style="{StaticResource SecondaryButtonStyle}"\n                                            Padding="12,5" Margin="0,0,0,4" FontSize="11"\n                                            ToolTip="\u5bf9\u5168\u90e8\u5df2\u5b58\u670d\u52a1\u5546\u9010\u4e2a\u6d4b\u8bd5\u8fde\u901a\u6027\uff08\u534f\u8bae/\u5730\u5740/Key/\u6a21\u578b\uff09\uff0c\u7ed3\u679c\u5217\u5728\u4e0b\u65b9">\n                                        <StackPanel Orientation="Horizontal">\n                                            <Path Data="{StaticResource IconSearch}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                            <TextBlock Text="\u4e00\u952e\u4f53\u68c0\u5168\u90e8\u670d\u52a1\u5546"/>\n                                        </StackPanel>\n                                    </Button>'
))

# TextBlock: list check results
replacements.append((
    '<TextBlock Text="\U0001f4cb \u6863\u6848\u4f53\u68c0\u7ed3\u679c" Style="{StaticResource CaptionText}" FontWeight="SemiBold" Margin="0,0,0,6"/>',
    '<StackPanel Orientation="Horizontal" Margin="0,0,0,6">\n                                                <Path Data="{StaticResource IconList}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                                <TextBlock Text="\u6863\u6848\u4f53\u68c0\u7ed3\u679c" Style="{StaticResource CaptionText}" FontWeight="SemiBold"/>\n                                            </StackPanel>'
))

# TextBlock: empty state with ➕
replacements.append((
    '<TextBlock Text="\u6682\u65e0\u670d\u52a1\u5546\u3002\u5728\u53f3\u4fa7\u586b\u597d\u914d\u7f6e\u540e\u70b9\u300c\u2795 \u6dfb\u52a0\u4e3a\u670d\u52a1\u5546\u300d\u5373\u53ef\u56fa\u5316\u590d\u7528\uff1b\u5bf9\u8bdd\u9875\u6a21\u578b\u4e0b\u62c9\u53ef\u76f4\u63a5\u70b9\u9009\u3002"',
    '<TextBlock Text="\u6682\u65e0\u670d\u52a1\u5546\u3002\u5728\u53f3\u4fa7\u586b\u597d\u914d\u7f6e\u540e\u70b9\u300c\u6dfb\u52a0\u4e3a\u670d\u52a1\u5546\u300d\u5373\u53ef\u56fa\u5316\u590d\u7528\uff1b\u5bf9\u8bdd\u9875\u6a21\u578b\u4e0b\u62c9\u53ef\u76f4\u63a5\u70b9\u9009\u3002"'
))

# Button: Download embed model
replacements.append((
    'Content="\u2b07\ufe0f \u4e0b\u8f7d\u5d4c\u5165\u6a21\u578b"\n                                            Command="{Binding DownloadModelCommand}"',
    'Command="{Binding DownloadModelCommand}"'
))
replacements.append((
    'ToolTip="\u624b\u52a8\u4e0b\u8f7d\u5f53\u524d\u9009\u62e9\u7684\u5d4c\u5165\u6a21\u578b\u5230\u672c\u5730\u7f13\u5b58\uff08\u9ed8\u8ba4\u9996\u6b21\u4f7f\u7528\u81ea\u52a8\u4e0b\u8f7d\uff1b\u6b64\u6309\u94ae\u7528\u4e8e\u65ad\u7f51\u9884\u4e0b\u8f7d/\u4fee\u590d\u7f13\u5b58\u573a\u666f\uff09"/>',
    'ToolTip="\u624b\u52a8\u4e0b\u8f7d\u5f53\u524d\u9009\u62e9\u7684\u5d4c\u5165\u6a21\u578b\u5230\u672c\u5730\u7f13\u5b58\uff08\u9ed8\u8ba4\u9996\u6b21\u4f7f\u7528\u81ea\u52a8\u4e0b\u8f7d\uff1b\u6b64\u6309\u94ae\u7528\u4e8e\u65ad\u7f51\u9884\u4e0b\u8f7d/\u4fee\u590d\u7f13\u5b58\u573a\u666f\uff09">\n                                        <StackPanel Orientation="Horizontal">\n                                            <Path Data="{StaticResource IconDownload}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                            <TextBlock Text="\u4e0b\u8f7d\u5d4c\u5165\u6a21\u578b"/>\n                                        </StackPanel>\n                                    </Button>'
))

# Button: Folder browse (watch path)
replacements.append((
    '<Button Grid.Column="1" Content="\U0001f4c1 \u6d4f\u89c8" Command="{Binding BrowseWatchPathCommand}"\n                                            Style="{StaticResource SecondaryButtonStyle}"\n                                            Width="Auto" Padding="12,6" Margin="0,0,8,0"/>',
    '<Button Grid.Column="1" Command="{Binding BrowseWatchPathCommand}"\n                                            Style="{StaticResource SecondaryButtonStyle}"\n                                            Width="Auto" Padding="12,6" Margin="0,0,8,0">\n                                        <StackPanel Orientation="Horizontal">\n                                            <Path Data="{StaticResource IconFolder}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                            <TextBlock Text="\u6d4f\u89c8"/>\n                                        </StackPanel>\n                                    </Button>'
))

# Button: Plus add (watch path)
replacements.append((
    '<Button Grid.Column="2" Content="\u2795 \u6dfb\u52a0" Command="{Binding AddWatchPathCommand}"\n                                            Style="{StaticResource PrimaryButtonStyle}"\n                                            Width="Auto" Padding="12,6"/>',
    '<Button Grid.Column="2" Command="{Binding AddWatchPathCommand}"\n                                            Style="{StaticResource PrimaryButtonStyle}"\n                                            Width="Auto" Padding="12,6">\n                                        <StackPanel Orientation="Horizontal">\n                                            <Path Data="{StaticResource IconPlus}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                            <TextBlock Text="\u6dfb\u52a0"/>\n                                        </StackPanel>\n                                    </Button>'
))

# Button: Trash (watch path list, standalone emoji)
replacements.append((
    '<Button Grid.Column="1" Content="\U0001f5d1\ufe0f"\n                                                        Command="{Binding DataContext.RemoveWatchPathCommand, RelativeSource={RelativeSource AncestorType=ListBox}}"\n                                                        CommandParameter="{Binding}"\n                                                        Background="Transparent" BorderThickness="0" Cursor="Hand" Padding="4,2"/>',
    '<Button Grid.Column="1"\n                                                        Command="{Binding DataContext.RemoveWatchPathCommand, RelativeSource={RelativeSource AncestorType=ListBox}}"\n                                                        CommandParameter="{Binding}"\n                                                        Background="Transparent" BorderThickness="0" Cursor="Hand" Padding="4,2">\n                                                    <Path Data="{StaticResource IconTrash}" Style="{StaticResource IconSmStyle}" Stroke="{StaticResource DangerBrush}"/>\n                                                </Button>'
))

# Button: Cancel install (⏹)
replacements.append((
    'Content="\u23f9 \u53d6\u6d88\u5b89\u88c5"',
    'Content="\u53d6\u6d88\u5b89\u88c5"'
))

# Button: Restart backend (GPU)
replacements.append((
    '<Button Content="\U0001f504 \u91cd\u542f\u540e\u7aef"\n                                                    Command="{Binding GpuWarning.RestartBackendCommand}"\n                                                    IsEnabled="{Binding GpuWarning.CanRestart}"\n                                                    Style="{StaticResource SecondaryButtonStyle}" Padding="10,6" Margin="0,0,8,0"/>',
    '<Button Command="{Binding GpuWarning.RestartBackendCommand}"\n                                                    IsEnabled="{Binding GpuWarning.CanRestart}"\n                                                    Style="{StaticResource SecondaryButtonStyle}" Padding="10,6" Margin="0,0,8,0">\n                                                <StackPanel Orientation="Horizontal">\n                                                    <Path Data="{StaticResource IconRefresh}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                                    <TextBlock Text="\u91cd\u542f\u540e\u7aef"/>\n                                                </StackPanel>\n                                            </Button>'
))

# Button: Re-detect (resource paths)
replacements.append((
    '<Button DockPanel.Dock="Right" Content="\U0001f504 \u91cd\u65b0\u68c0\u6d4b"\n                                            Command="{Binding ResourcePaths.RefreshCommand}"\n                                            Style="{StaticResource SecondaryButtonStyle}" Padding="10,4" FontSize="11"/>',
    '<Button DockPanel.Dock="Right"\n                                            Command="{Binding ResourcePaths.RefreshCommand}"\n                                            Style="{StaticResource SecondaryButtonStyle}" Padding="10,4" FontSize="11">\n                                        <StackPanel Orientation="Horizontal">\n                                            <Path Data="{StaticResource IconRefresh}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                            <TextBlock Text="\u91cd\u65b0\u68c0\u6d4b"/>\n                                        </StackPanel>\n                                    </Button>'
))

# Button: Search auto-locate
replacements.append((
    '<Button Grid.Column="1" Content="\U0001f50d \u81ea\u52a8\u5bfb\u627e\u53ef\u7528\u914d\u7f6e"\n                                                            Command="{Binding DataContext.ResourcePaths.AutoLocateCommand, RelativeSource={RelativeSource AncestorType=ItemsControl}}"\n                                                            CommandParameter="{Binding}"\n                                                            Style="{StaticResource SecondaryButtonStyle}" Padding="10,4" FontSize="11"\n                                                            Margin="0,0,8,0"/>',
    '<Button Grid.Column="1"\n                                                            Command="{Binding DataContext.ResourcePaths.AutoLocateCommand, RelativeSource={RelativeSource AncestorType=ItemsControl}}"\n                                                            CommandParameter="{Binding}"\n                                                            Style="{StaticResource SecondaryButtonStyle}" Padding="10,4" FontSize="11"\n                                                            Margin="0,0,8,0">\n                                                        <StackPanel Orientation="Horizontal">\n                                                            <Path Data="{StaticResource IconSearch}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                                            <TextBlock Text="\u81ea\u52a8\u5bfb\u627e\u53ef\u7528\u914d\u7f6e"/>\n                                                        </StackPanel>\n                                                    </Button>'
))

# Button: Folder browse (resource paths)
replacements.append((
    '<Button Grid.Column="2" Content="\U0001f4c1 \u6d4f\u89c8\u9009\u62e9\u2026"\n                                                            Command="{Binding DataContext.ResourcePaths.BrowseCommand, RelativeSource={RelativeSource AncestorType=ItemsControl}}"\n                                                            CommandParameter="{Binding}"\n                                                            Style="{StaticResource SecondaryButtonStyle}" Padding="10,4" FontSize="11"\n                                                            Margin="0,0,8,0"/>',
    '<Button Grid.Column="2"\n                                                            Command="{Binding DataContext.ResourcePaths.BrowseCommand, RelativeSource={RelativeSource AncestorType=ItemsControl}}"\n                                                            CommandParameter="{Binding}"\n                                                            Style="{StaticResource SecondaryButtonStyle}" Padding="10,4" FontSize="11"\n                                                            Margin="0,0,8,0">\n                                                        <StackPanel Orientation="Horizontal">\n                                                            <Path Data="{StaticResource IconFolder}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                                            <TextBlock Text="\u6d4f\u89c8\u9009\u62e9\u2026"/>\n                                                        </StackPanel>\n                                                    </Button>'
))

# Button: Stop search (⏹)
replacements.append((
    'Content="\u23f9 \u505c\u6b62\u641c\u7d22"',
    'Content="\u505c\u6b62\u641c\u7d22"'
))

# Button: Refresh dependencies
replacements.append((
    '<Button DockPanel.Dock="Right" Content="\U0001f504 \u5237\u65b0"\n                                            Command="{Binding RefreshDependenciesCommand}"\n                                            IsEnabled="{Binding IsDependenciesLoading, Converter={StaticResource BoolInvertConverter}}"\n                                            Style="{StaticResource TextButtonStyle}" FontSize="11" Padding="6,2"\n                                            HorizontalAlignment="Right" VerticalAlignment="Center"/>',
    '<Button DockPanel.Dock="Right"\n                                            Command="{Binding RefreshDependenciesCommand}"\n                                            IsEnabled="{Binding IsDependenciesLoading, Converter={StaticResource BoolInvertConverter}}"\n                                            Style="{StaticResource TextButtonStyle}" FontSize="11" Padding="6,2"\n                                            HorizontalAlignment="Right" VerticalAlignment="Center">\n                                        <StackPanel Orientation="Horizontal">\n                                            <Path Data="{StaticResource IconRefresh}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                            <TextBlock Text="\u5237\u65b0"/>\n                                        </StackPanel>\n                                    </Button>'
))

# Button: Stethoscope full checkup
replacements.append((
    '<Button DockPanel.Dock="Right" Content="\U0001fa7a \u6267\u884c\u5168\u9762\u4f53\u68c0"\n                                            Command="{Binding RunDoctorCommand}"\n                                            IsEnabled="{Binding IsRunningDoctor, Converter={StaticResource BoolInvertConverter}}"\n                                            Style="{StaticResource PrimaryButtonStyle}"\n                                            FontSize="11"\n                                            Padding="10,4"\n                                            HorizontalAlignment="Right"\n                                            VerticalAlignment="Center"/>',
    '<Button DockPanel.Dock="Right"\n                                            Command="{Binding RunDoctorCommand}"\n                                            IsEnabled="{Binding IsRunningDoctor, Converter={StaticResource BoolInvertConverter}}"\n                                            Style="{StaticResource PrimaryButtonStyle}"\n                                            FontSize="11"\n                                            Padding="10,4"\n                                            HorizontalAlignment="Right"\n                                            VerticalAlignment="Center">\n                                        <StackPanel Orientation="Horizontal">\n                                            <Path Data="{StaticResource IconStethoscope}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                            <TextBlock Text="\u6267\u884c\u5168\u9762\u4f53\u68c0"/>\n                                        </StackPanel>\n                                    </Button>'
))

# Button: Restart backend (service panel)
replacements.append((
    'Content="\U0001f504 \u5f3a\u529b\u91cd\u542f\u540e\u7aef\u670d\u52a1"\n                                            Command="{Binding RestartBackendCommand}"',
    'Command="{Binding RestartBackendCommand}"'
))
replacements.append((
    'ToolTip="\u5f3a\u529b\u6e05\u7406\u53ef\u80fd\u6b8b\u7559\u7684\u65e7\u8fdb\u7a0b\uff0c\u91ca\u653e 8765 \u7aef\u53e3\u5e76\u91cd\u65b0\u62c9\u8d77\u6700\u65b0\u540e\u7aef"/>',
    'ToolTip="\u5f3a\u529b\u6e05\u7406\u53ef\u80fd\u6b8b\u7559\u7684\u65e7\u8fdb\u7a0b\uff0c\u91ca\u653e 8765 \u7aef\u53e3\u5e76\u91cd\u65b0\u62c9\u8d77\u6700\u65b0\u540e\u7aef">\n                                        <StackPanel Orientation="Horizontal">\n                                            <Path Data="{StaticResource IconRefresh}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                                            <TextBlock Text="\u5f3a\u529b\u91cd\u542f\u540e\u7aef\u670d\u52a1"/>\n                                        </StackPanel>\n                                    </Button>'
))

# Button: Save all
replacements.append((
    '<Button Content="\U0001f4be \u4fdd\u5b58\u6240\u6709\u4fee\u6539"\n                            Command="{Binding SaveCommand}"\n                            Style="{StaticResource PrimaryButtonStyle}"\n                            Padding="16,6"\n                            Margin="0,0,10,0"/>',
    '<Button Command="{Binding SaveCommand}"\n                            Style="{StaticResource PrimaryButtonStyle}"\n                            Padding="16,6"\n                            Margin="0,0,10,0">\n                        <StackPanel Orientation="Horizontal">\n                            <Path Data="{StaticResource IconSave}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                            <TextBlock Text="\u4fdd\u5b58\u6240\u6709\u4fee\u6539"/>\n                        </StackPanel>\n                    </Button>'
))

# Button: Revert
replacements.append((
    '<Button Content="\u21ba \u6062\u590d\u4e0a\u6b21\u4fdd\u5b58"\n                            Command="{Binding RevertCommand}"\n                            Style="{StaticResource TextButtonStyle}"\n                            Padding="10,6"/>',
    '<Button Command="{Binding RevertCommand}"\n                            Style="{StaticResource TextButtonStyle}"\n                            Padding="10,6">\n                        <StackPanel Orientation="Horizontal">\n                            <Path Data="{StaticResource IconRotateCcw}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>\n                            <TextBlock Text="\u6062\u590d\u4e0a\u6b21\u4fdd\u5b58"/>\n                        </StackPanel>\n                    </Button>'
))

# Warning text
replacements.append((
    '<TextBlock Text="\u26a0\ufe0f \u6e05\u9664 API Key \u540e\uff0c\u4fdd\u5b58\u65f6\u4f1a\u540c\u65f6\u5220\u9664\u672c\u5730\u548c\u540e\u7aef\u7684 Key\u3002"',
    '<TextBlock Text="\u6e05\u9664 API Key \u540e\uff0c\u4fdd\u5b58\u65f6\u4f1a\u540c\u65f6\u5220\u9664\u672c\u5730\u548c\u540e\u7aef\u7684 Key\u3002"'
))

# Hint text about local AI
replacements.append((
    '<TextBlock Text="\U0001f4a1 \u672a\u68c0\u6d4b\u5230\u8fd0\u884c\u4e2d\u7684 LM Studio \u6216 Ollama \u670d\u52a1\u3002\u82e5\u9700\u4f7f\u7528\u672c\u5730\u5927\u6a21\u578b\uff0c\u53ef\u5148\u542f\u52a8\u5b83\u4eec\u5e76\u5f00\u542f Local Server \u540e\u70b9\u51fb\u300c\u91cd\u65b0\u63a2\u6d4b\u300d\u3002"\n                                           Visibility="{Binding HasLocalAiDetected, Converter={StaticResource InvertBoolVis}}"\n                                           FontSize="11" Foreground="{StaticResource TextTertiaryBrush}" TextWrapping="Wrap" Margin="0,2,0,0"/>',
    '<StackPanel Orientation="Horizontal" Visibility="{Binding HasLocalAiDetected, Converter={StaticResource InvertBoolVis}}" Margin="0,2,0,0">\n                                    <Path Data="{StaticResource IconLightbulb}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0" VerticalAlignment="Top"/>\n                                    <TextBlock Text="\u672a\u68c0\u6d4b\u5230\u8fd0\u884c\u4e2d\u7684 LM Studio \u6216 Ollama \u670d\u52a1\u3002\u82e5\u9700\u4f7f\u7528\u672c\u5730\u5927\u6a21\u578b\uff0c\u53ef\u5148\u542f\u52a8\u5b83\u4eec\u5e76\u5f00\u542f Local Server \u540e\u70b9\u51fb\u300c\u91cd\u65b0\u63a2\u6d4b\u300d\u3002"\n                                               FontSize="11" Foreground="{StaticResource TextTertiaryBrush}" TextWrapping="Wrap"/>\n                                </StackPanel>'
))

# === Apply all replacements ===
count = 0
for old, new in replacements:
    if old in content:
        content = content.replace(old, new, 1)  # Replace only first occurrence
        count += 1
    #else:
    #    print(f"NOT FOUND: {old[:60]}...")

print(f"Applied {count} replacements out of {len(replacements)}")

# Write result
with open(FILE, 'w', encoding='utf-8') as f:
    f.write(content)

print("Done!")
